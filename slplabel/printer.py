"""Printer access: serialises jobs and hides the transport (usbfs / usblp / mock)."""

import errno
import os
import select
import struct
import threading
import time

from . import usbfs
from .protocol import CMD_FORMFEED, CMD_NOP, decode_job

try:
    import fcntl
except ImportError:
    fcntl = None

LPGETSTATUS = 0x060B


class PrinterError(RuntimeError):
    """Error with a message that can be shown to the user."""


class _UsblpConnection:
    """Fallback transport through the kernel's usblp device file."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def port_status(self):
        buf = bytearray(4)
        fcntl.ioctl(self.fd, LPGETSTATUS, buf)
        return struct.unpack("i", buf)[0] & 0xFF

    def _wait(self, timeout_ms):
        if not select.select([], [self.fd], [], timeout_ms / 1000)[1]:
            raise OSError(errno.ETIMEDOUT, "timeout")

    def write(self, data, timeout_ms=15000):
        pos = 0
        while pos < len(data):
            self._wait(timeout_ms)
            try:
                pos += os.write(self.fd, data[pos:pos + 1024])
            except BlockingIOError:
                time.sleep(0.02)
        self._wait(timeout_ms)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _MockConnection:
    def __init__(self, printer):
        self.printer = printer

    def port_status(self):
        return self.printer.mock_status

    def write(self, data, timeout_ms=15000):
        if self.printer.mock_error:
            raise self.printer.mock_error
        self.printer.mock_labels += decode_job(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class Printer:
    def __init__(self, backend="auto", usb_id="0619:0103", device="/dev/usb/lp0"):
        if backend not in ("auto", "usbfs", "usblp", "mock"):
            raise ValueError(f"unknown backend {backend!r}")
        self.backend, self.device = backend, device
        self.vid, self.pid = usb_id.lower().split(":")
        self.lock = threading.Lock()
        self._resync = False
        self.mock_labels, self.mock_status, self.mock_error = [], 0x18, None

    # -- transport ---------------------------------------------------------
    def _open(self):
        """Return (connection, product name)."""
        if self.backend == "mock":
            return _MockConnection(self), "Simulator"
        if self.backend in ("auto", "usbfs"):
            devs = usbfs.find_printers(self.vid, self.pid)
            if devs and (self.backend == "usbfs" or devs[0].node.exists()):
                return usbfs.UsbfsConnection(devs[0]), devs[0].product
        if self.backend in ("auto", "usblp") and os.path.exists(self.device):
            return _UsblpConnection(self.device), "SLP100 (usblp)"
        raise PrinterError("Drucker nicht gefunden. Ist er eingeschaltet, angesteckt und durchgereicht?")

    @staticmethod
    def _explain(e: OSError) -> PrinterError:
        return PrinterError({
            errno.EACCES: "Kein Zugriff auf den Drucker (Rechte bzw. Gruppe lp prüfen).",
            errno.EPERM: "Docker verweigert den USB-Zugriff (device_cgroup_rules prüfen).",
            errno.EBUSY: "Der Drucker wird gerade von einem anderen Programm benutzt.",
            errno.ETIMEDOUT: "Der Drucker nimmt keine Daten an (Etiketten eingelegt, Klappe zu?).",
            errno.ENODEV: "Der Drucker wurde getrennt.",
            errno.ENOENT: "Der USB-Anschluss ist im Container nicht sichtbar (/dev/bus/usb einbinden).",
        }.get(e.errno, f"USB-Fehler: {e.strerror or e}"))

    @staticmethod
    def _check(status: int):
        if status & 0x20:
            raise PrinterError("Keine Etiketten eingelegt.")
        if not status & 0x08:
            raise PrinterError("Der Drucker meldet einen Fehler (Klappe offen oder Papierstau?).")

    # -- public API --------------------------------------------------------
    def status(self) -> dict:
        if not self.lock.acquire(timeout=0.3):
            return {"connected": True, "ready": False, "busy": True, "message": "Druckt gerade …"}
        try:
            conn, product = self._open()
            with conn:
                st = conn.port_status()
            out = {"connected": True, "ready": True, "busy": False, "product": product,
                   "port_status": f"0x{st:02x}", "message": "Bereit"}
            try:
                self._check(st)
            except PrinterError as e:
                out.update(ready=False, message=str(e))
            return out
        except (PrinterError, OSError) as e:
            err = self._explain(e) if isinstance(e, OSError) else e
            return {"connected": False, "ready": False, "busy": False, "message": str(err)}
        finally:
            self.lock.release()

    def print_job(self, job: bytes, copies: int = 1) -> int:
        """Send a job `copies` times. Raises PrinterError, naming finished copies."""
        done = 0
        with self.lock:
            try:
                for _ in range(copies):
                    self._send(job)
                    done += 1
            except (PrinterError, OSError) as e:
                err = self._explain(e) if isinstance(e, OSError) else e
                if copies > 1:
                    raise PrinterError(f"{err} ({done} von {copies} gedruckt)") from e
                raise err from e
        return done

    def _send(self, job: bytes):
        conn, _ = self._open()
        with conn:
            self._check(conn.port_status())
            if self._resync:
                # A previous job broke off: NOPs complete a half-sent raster
                # row, the form feed ejects the spoilt label.
                conn.write(bytes([CMD_NOP] * 32 + [CMD_FORMFEED]))
                self._resync = False
            try:
                conn.write(job)
            except OSError:
                self._resync = True
                raise

    def feed(self):
        self.print_job(bytes([CMD_FORMFEED]))
