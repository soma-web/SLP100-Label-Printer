"""Minimal user-space USB printer driver on top of Linux usbfs.

Talks to /dev/bus/usb directly via ioctl, so neither the usblp kernel module
nor libusb is needed. Devices are looked up in sysfs on every access, which
makes unplugging and replugging work without restarting the container.
"""

import ctypes
import errno
import os
import struct
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:            # not on Linux (development machine)
    fcntl = None

SYSFS = Path("/sys/bus/usb/devices")
DEVFS = Path("/dev/bus/usb")

# ioctl numbers for 64-bit kernels (amd64, arm64)
USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_BULK = 0xC0185502
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510
USBDEVFS_IOCTL = 0xC0105512
USBDEVFS_DISCONNECT = 0x5516


class _Control(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


class _Bulk(ctypes.Structure):
    _fields_ = [("ep", ctypes.c_uint), ("len", ctypes.c_uint),
                ("timeout", ctypes.c_uint), ("data", ctypes.c_void_p)]


class _Ioctl(ctypes.Structure):
    _fields_ = [("ifno", ctypes.c_int), ("ioctl_code", ctypes.c_int),
                ("data", ctypes.c_void_p)]


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


@dataclass
class UsbPrinter:
    sys_path: Path
    node: Path
    interface: int
    ep_out: int
    product: str

    @property
    def driver(self) -> str | None:
        for intf in self.sys_path.glob("*:*"):
            if int(_read(intf / "bInterfaceNumber") or "0", 16) == self.interface:
                link = intf / "driver"
                return os.path.basename(os.readlink(link)) if link.is_symlink() else None
        return None


def find_printers(vid: str, pid: str) -> list[UsbPrinter]:
    found = []
    if not SYSFS.is_dir():
        return found
    for dev in sorted(SYSFS.iterdir()):
        if _read(dev / "idVendor") != vid or _read(dev / "idProduct") != pid:
            continue
        for intf in sorted(dev.glob("*:*")):
            if _read(intf / "bInterfaceClass") != "07":
                continue
            ep_out = next((int(ep.name[3:], 16) for ep in sorted(intf.glob("ep_*"))
                           if _read(ep / "type") == "Bulk" and _read(ep / "direction") == "out"), None)
            if ep_out is None:
                continue
            node = DEVFS / f"{int(_read(dev / 'busnum')):03d}" / f"{int(_read(dev / 'devnum')):03d}"
            found.append(UsbPrinter(dev, node, int(_read(intf / "bInterfaceNumber"), 16), ep_out,
                                    _read(dev / "product") or "USB printer"))
            break
    return found


class UsbfsConnection:
    """Open, claimed connection to one printer interface."""

    CHUNK = 1024

    def __init__(self, dev: UsbPrinter):
        self.dev = dev
        self.fd = os.open(dev.node, os.O_RDWR | os.O_CLOEXEC)
        try:
            self._claim()
        except BaseException:
            os.close(self.fd)
            raise

    def _claim(self):
        if self.dev.driver == "usblp":     # only ever detach the printer class driver
            cmd = _Ioctl(self.dev.interface, USBDEVFS_DISCONNECT, None)
            try:
                fcntl.ioctl(self.fd, USBDEVFS_IOCTL, cmd)
            except OSError as e:
                if e.errno != errno.ENODATA:   # already detached
                    raise
        fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", self.dev.interface))

    def port_status(self) -> int:
        """USB printer class request GET_PORT_STATUS."""
        buf = (ctypes.c_uint8 * 1)()
        req = _Control(0xA1, 1, 0, self.dev.interface, 1, 2000,
                       ctypes.cast(buf, ctypes.c_void_p))
        if fcntl.ioctl(self.fd, USBDEVFS_CONTROL, req) != 1:
            raise OSError(errno.EIO, "no port status")
        return buf[0]

    def write(self, data: bytes, timeout_ms: int = 15000):
        for i in range(0, len(data), self.CHUNK):
            chunk = data[i:i + self.CHUNK]
            buf = ctypes.create_string_buffer(chunk, len(chunk))
            req = _Bulk(self.dev.ep_out, len(chunk), timeout_ms, ctypes.cast(buf, ctypes.c_void_p))
            sent = fcntl.ioctl(self.fd, USBDEVFS_BULK, req)
            if sent != len(chunk):
                raise OSError(errno.EIO, f"short USB write ({sent}/{len(chunk)})")

    def close(self):
        try:
            fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE, struct.pack("I", self.dev.interface))
        except OSError:
            pass
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
