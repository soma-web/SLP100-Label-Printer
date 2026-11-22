import base64
import ctypes
import errno
import os

import pytest
from PIL import Image
from pydantic import ValidationError

from slplabel import protocol, render, usbfs
from slplabel.config import Config
from slplabel.printer import Printer, PrinterError
from slplabel.render import LabelError, LabelSpec
from slplabel.service import Service


@pytest.fixture
def service(tmp_path):
    return Service(Config(data_dir=tmp_path, backend="mock"))


def data_url(img):
    return "data:image/png;base64," + base64.b64encode(render.png_bytes(img)).decode()


SPECS = [
    dict(lines=["Hallo Welt"]),
    dict(lines=["Äpfel", "Öl", "Übung ß"], length_mm=83, border=True),
    dict(lines=["A"], qr="https://example.com", length_mm=83),
    dict(qr="nur QR"),
    dict(lines=["Labor"], icon="labor", length_mm=44, graphic_pos="right"),
    dict(lines=["X"], invert=True, qr="abc", length_mm=83),
]


@pytest.mark.parametrize("kw", SPECS)
def test_printed_dots_equal_preview(service, kw):
    spec = LabelSpec(**kw)
    img, _ = service.render(spec)
    service.print_label(spec)
    got = service.printer.mock_labels[-1]
    assert got.size == img.size and got.tobytes() == img.tobytes()


def test_orientation_in_raw_stream():
    img = Image.new("1", (3, protocol.DOTS), 1)
    img.putpixel((0, protocol.DOTS - 1), 0)      # bottom-left dot of the label
    job = protocol.to_job(img)
    row = job[job.index(bytes([protocol.CMD_PRINT, 24])) + 2:][:24]
    assert row == b"\x80" + bytes(23)            # first line, first head dot


def test_long_blank_runs_use_valid_counts():
    img = Image.new("1", (1000, protocol.DOTS), 1)
    img.putpixel((999, 5), 0)
    (decoded,) = protocol.decode_job(protocol.to_job(img))
    assert decoded.size == img.size and decoded.tobytes() == img.tobytes()


@pytest.mark.parametrize("scale", [0.3, 0.5, 0.6, 0.75, 0.9, 1.0])
def test_qr_never_exceeds_requested_height(scale):
    h = int((protocol.DOTS - 16) * scale)
    img = render.qr_image("https://example.com/regal3", h)
    assert img.width == img.height <= h and img.width % 25 == 0


def test_qr_too_long_is_an_error_not_clipped(service):
    with pytest.raises(LabelError):
        service.render(LabelSpec(qr="x" * 900, graphic_scale=0.3))


def test_inverted_qr_keeps_black_on_white(service):
    a, _ = service.render(LabelSpec(qr="abc", length_mm=83))
    b, _ = service.render(LabelSpec(qr="abc", length_mm=83, invert=True))
    size = render.qr_image("abc", int((protocol.DOTS - 16) * 0.9)).width
    x, y = (a.width - size) // 2, (protocol.DOTS - size) // 2
    box = (x, y, x + size, y + size)
    assert a.crop(box).tobytes() == b.crop(box).tobytes()


def test_area_stays_blank(service):
    service.update_settings({"area_top_mm": 3, "area_bottom_mm": 2})
    img, _ = service.render(LabelSpec(lines=["Test"], border=True, invert=True, length_mm=83))
    top, bottom = render.mm_to_dots(3), render.mm_to_dots(2)
    assert img.crop((0, 0, img.width, top)).getextrema() == (255, 255)
    assert img.crop((0, protocol.DOTS - bottom, img.width, protocol.DOTS)).getextrema() == (255, 255)


def test_warning_when_text_does_not_fit(service):
    _, warnings = service.render(LabelSpec(lines=["Sehr langer Text"], size_pt=60, length_mm=44))
    assert warnings


def test_bad_images_and_values_are_rejected(service):
    for img in ("data:image/png;base64,AAAA", "data:image/png;base64,***", "nix"):
        with pytest.raises(LabelError):
            service.render(LabelSpec(image=img))
    for kw in (dict(length_mm=9999), dict(align="x"), dict(graphic_scale=5), dict(lines=["x" * 400])):
        with pytest.raises(ValidationError):
            LabelSpec(**kw)
    with pytest.raises(LabelError):
        service.print_label(LabelSpec(lines=["", "  "]))
    with pytest.raises(LabelError):
        service.print_label(LabelSpec(lines=["a"]), copies=51)


def test_transparent_image_prints_white(service):
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    out, _ = service.render(LabelSpec(image=data_url(img)))
    assert out.getextrema() == (255, 255)


def test_templates_of_windows_version_import(service):
    old = {"Regal": {"lines": ["A", "B"], "font": "Arial", "qr": "x", "image": None, "gmode": "qr",
                     "length_mm": 83, "image_rotate": 0, "size_pt": 0}}
    assert service.save_templates(old) == {"saved": 1, "skipped": 0}
    service.print_label(service.get_template("Regal"))
    assert service.save_templates(old, overwrite=False) == {"saved": 0, "skipped": 1}


def test_icons(service):
    assert {"labor", "detektiv"} <= set(service.icons())
    name = service.add_icon("../../Böse Datei", data_url(Image.new("L", (20, 20))))
    assert service.icons()[name].parent == service.icon_dir
    with pytest.raises(LabelError):
        service.add_icon("labor", data_url(Image.new("L", (20, 20))))
    with pytest.raises(LabelError):
        service.delete_icon("labor")
    service.delete_icon(name)
    assert name not in service.icons()


def test_icon_set(service):
    index = render.icon_index()
    assert len(index) > 500
    assert {p.stem for p in render.ICON_SET.glob("*.png")} == set(index)
    icons = service.icon_list()
    assert [i["name"] for i in icons[:2]] == ["detektiv", "labor"]
    assert len(icons) == len(index) + 2 and all(i["label"] and i["category"] for i in icons)
    assert next(i for i in icons if i["name"] == "schluessel")["label"] == "Schlüssel"
    with pytest.raises(LabelError):
        service.add_icon("katze", data_url(Image.new("L", (20, 20))))
    service.print_label(LabelSpec(icon="katze", lines=["Mia"], length_mm=83))


def test_own_icon_with_name_of_the_set_stays(service):
    Image.new("L", (20, 20)).save(service.icon_dir / "katze.png")   # saved by an older version
    (first, *rest) = service.icon_list()
    assert first["name"] == "katze" and first["own"] and "katze" not in [i["name"] for i in rest]
    service.delete_icon("katze")
    assert service.icons()["katze"].parent == render.ICON_SET


def test_default_label_is_small_address(service):
    assert service.settings.default_length_mm == 83
    # saved templates rely on these defaults, the editor starts with other values
    assert LabelSpec().length_mm == 0 and LabelSpec().margin_mm == 1


def test_settings_persist_and_validate(service):
    service.update_settings({"density": 3, "unknown": 1})
    assert Service(service.config).settings.density == 3
    with pytest.raises(ValidationError):
        service.update_settings({"density": 9})
    assert service.settings.density == 3


def test_token_is_stable(service):
    assert len(service.mcp_token) > 30
    assert Service(service.config).mcp_token == service.mcp_token


# -- printer -----------------------------------------------------------------
def test_paper_out_and_error_bits():
    p = Printer("mock")
    assert p.status()["ready"]
    p.mock_status = 0x38
    assert not p.status()["ready"]
    with pytest.raises(PrinterError, match="Etiketten"):
        p.feed()
    p.mock_status = 0x10
    with pytest.raises(PrinterError, match="Fehler"):
        p.feed()


def test_jam_is_shown_and_refused_but_short_faults_are_waited_out():
    p = Printer("mock")
    p.jam_wait = 0
    p.mock_bytes = [0x52]
    s = p.status()
    assert s["connected"] and not s["ready"] and "0x52" in s["message"]
    with pytest.raises(PrinterError, match="Störung"):
        p.feed()
    p.jam_wait, p.mock_bytes = 5, [0x52, 0x50]
    p.feed()
    for byte in (0x50, 0x51, None):          # must never block printing
        p.jam_wait, p.mock_bytes = 0, [byte]
        assert p.status()["ready"]
        p.feed()


@pytest.mark.parametrize("code", [errno.ENODEV, errno.ESHUTDOWN, errno.EPROTO, errno.EIO])
def test_lost_usb_connection_has_clear_message(code):
    p = Printer("mock")
    p.mock_error = OSError(code, "Cannot send after transport endpoint shutdown")
    with pytest.raises(PrinterError, match="Verlängerung"):
        p.feed()


def test_broken_job_is_resynced_and_reports_copies():
    p = Printer("mock")
    job = protocol.to_job(Image.new("1", (10, protocol.DOTS), 0))
    p.mock_error = OSError(errno.ETIMEDOUT, "timeout")
    with pytest.raises(PrinterError, match="0 von 3"):
        p.print_job(job, 3)
    assert p._resync
    p.mock_error = None
    assert p.print_job(job, 2) == 2
    assert not p._resync and len(p.mock_labels) == 2


def test_missing_printer_has_clear_message(tmp_path):
    s = Printer("usblp", device=str(tmp_path / "nope")).status()
    assert not s["connected"] and "nicht gefunden" in s["message"]


# -- usbfs -------------------------------------------------------------------
@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit only")
def test_ioctl_numbers_match_struct_sizes():
    def ioc(d, nr, size):
        return d << 30 | size << 16 | ord("U") << 8 | nr
    assert usbfs.USBDEVFS_CONTROL == ioc(3, 0, ctypes.sizeof(usbfs._Control))
    assert usbfs.USBDEVFS_BULK == ioc(3, 2, ctypes.sizeof(usbfs._Bulk))
    assert usbfs.USBDEVFS_IOCTL == ioc(3, 18, ctypes.sizeof(usbfs._Ioctl))
    assert usbfs.USBDEVFS_CLAIMINTERFACE == ioc(2, 15, 4)
    assert usbfs.USBDEVFS_RELEASEINTERFACE == ioc(2, 16, 4)


@pytest.mark.skipif(os.name == "nt", reason="':' in file names")
def test_finds_printer_in_sysfs(tmp_path, monkeypatch):
    def put(path, **files):
        path.mkdir(parents=True)
        for k, v in files.items():
            (path / k).write_text(v + "\n")
    dev = tmp_path / "2-1"
    put(dev, idVendor="0619", idProduct="0103", busnum="2", devnum="14", product="SLP")
    put(dev / "2-1:1.0", bInterfaceClass="07", bInterfaceNumber="00")
    put(dev / "2-1:1.0" / "ep_02", type="Bulk", direction="out")
    put(dev / "2-1:1.0" / "ep_81", type="Bulk", direction="in")
    put(tmp_path / "usb1", idVendor="1d6b", idProduct="0002")
    (tmp_path / "usblp").mkdir()
    (dev / "2-1:1.0" / "driver").symlink_to(tmp_path / "usblp")
    monkeypatch.setattr(usbfs, "SYSFS", tmp_path)
    (p,) = usbfs.find_printers("0619", "0103")
    assert p.node.as_posix() == "/dev/bus/usb/002/014"
    assert (p.interface, p.ep_out, p.ep_in, p.driver) == (0, 2, 0x81, "usblp")
