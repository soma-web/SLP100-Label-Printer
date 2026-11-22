"""Label layout and rendering (192 dots high, length = feed direction)."""

import base64
import binascii
import io
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .protocol import DOTS, DPI

Image.MAX_IMAGE_PIXELS = 40_000_000

FONTS = {   # name -> (regular, bold); only fonts that are installed are offered
    "Liberation Sans": ("LiberationSans-Regular", "LiberationSans-Bold"),
    "Liberation Serif": ("LiberationSerif-Regular", "LiberationSerif-Bold"),
    "Liberation Mono": ("LiberationMono-Regular", "LiberationMono-Bold"),
    "DejaVu Sans": ("DejaVuSans", "DejaVuSans-Bold"),
    "Open Sans": ("OpenSans-Regular", "OpenSans-Bold"),
    "Comic Neue": ("ComicNeue-Regular", "ComicNeue-Bold"),
    "Arial": ("arial", "arialbd"),
    "Segoe UI": ("segoeui", "segoeuib"),
}
# Fonts of the Windows version -> free replacement
ALIASES = {
    "Arial": "Liberation Sans", "Times New Roman": "Liberation Serif",
    "Courier New": "Liberation Mono", "Consolas": "Liberation Mono",
    "Verdana": "DejaVu Sans", "Tahoma": "DejaVu Sans", "Segoe UI": "Open Sans",
    "Calibri": "Open Sans", "Bahnschrift": "Open Sans", "Impact": "Liberation Sans",
}
# Pure black/white drawings with index.json (German labels), see tools/build_icons.py
ICON_SET = Path(__file__).parent / "icons" / "set"
MEDIA = [("Adresse klein", 24, 83), ("Mehrzweck", 24, 44)]


class LabelError(ValueError):
    """Problem with the label content; message can be shown to the user."""


class LabelSpec(BaseModel):
    """Everything that describes one label."""

    model_config = ConfigDict(extra="ignore")

    lines: list[str] = Field(default_factory=list, max_length=20)
    font: str = Field("", max_length=60)
    bold: bool = True
    size_pt: float = Field(0, ge=0, le=100)
    align: Literal["left", "center", "right"] = "center"
    line_spacing: float = Field(1.0, ge=0.5, le=3)
    margin_mm: float = Field(1.0, ge=0, le=8)
    length_mm: float = Field(0, ge=0, le=300)
    border: bool = False
    invert: bool = False
    qr: str = Field("", max_length=1000)
    icon: str = Field("", max_length=80)
    image: str = Field("", max_length=8_000_000)
    image_rotate: int = Field(0, ge=0, le=3)
    graphic_pos: Literal["left", "right"] = "left"
    graphic_scale: float = Field(0.9, ge=0.3, le=1.0)

    @field_validator("qr", "icon", "image", "font", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v

    @field_validator("lines")
    @classmethod
    def _limit_lines(cls, v):
        if any(len(line) > 300 for line in v):
            raise ValueError("Zeile zu lang")
        return v


def mm_to_dots(mm: float) -> int:
    return round(float(mm) * DPI / 25.4)


def dots_to_mm(dots: int) -> float:
    return dots * 25.4 / DPI


# -- fonts -------------------------------------------------------------------
@lru_cache(maxsize=1)
def _font_files() -> dict[str, str]:
    roots = [os.environ.get("SLP_FONT_DIR", ""), "/usr/share/fonts",
             os.path.join(os.environ.get("WINDIR", ""), "Fonts") if os.name == "nt" else ""]
    files = {}
    for root in filter(None, roots):
        for base, _, names in os.walk(root):
            for n in names:
                stem, ext = os.path.splitext(n)
                if ext.lower() in (".ttf", ".otf"):
                    files.setdefault(stem.lower(), os.path.join(base, n))
    return files


def available_fonts() -> list[str]:
    files = _font_files()
    return [n for n, (reg, _) in FONTS.items() if reg.lower() in files]


@lru_cache(maxsize=256)
def _font(name: str, bold: bool, size: int):
    files = _font_files()
    candidates = [name, ALIASES.get(name, ""), *FONTS]
    for cand in candidates:
        if cand in FONTS:
            reg, bd = FONTS[cand]
            path = files.get((bd if bold else reg).lower()) or files.get(reg.lower())
            if path:
                return ImageFont.truetype(path, max(size, 4))
    return ImageFont.load_default(max(size, 4))


# -- graphics ----------------------------------------------------------------
@lru_cache(maxsize=1)
def icon_index() -> dict[str, dict]:
    return json.loads((ICON_SET / "index.json").read_text(encoding="utf-8"))


def _flatten(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L")


def load_image(raw: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw), formats=["PNG", "JPEG", "GIF", "BMP", "WEBP"]) as img:
            return _flatten(ImageOps.exif_transpose(img))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise LabelError("Das Bild kann nicht gelesen werden (PNG, JPEG, GIF, BMP oder WebP).") from e


def decode_data_url(data_url: str) -> bytes:
    try:
        return base64.b64decode(data_url.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as e:
        raise LabelError("Ungültige Bilddaten.") from e


def qr_image(text: str, size: int) -> Image.Image:
    """QR code without quiet zone, at most `size` dots; modules are whole dots."""
    import qrcode
    from qrcode.exceptions import DataOverflowError

    q = qrcode.QRCode(border=0, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    try:
        q.make(fit=True)
    except (DataOverflowError, ValueError) as e:
        raise LabelError("Der QR-Inhalt ist zu lang.") from e
    matrix = q.get_matrix()
    n = len(matrix)
    scale = size // n
    if scale < 1:
        raise LabelError("Der QR-Inhalt ist zu lang für diese Größe.")
    img = Image.new("1", (n, n))
    img.putdata([0 if cell else 1 for row in matrix for cell in row])
    return img.resize((n * scale, n * scale), Image.Resampling.NEAREST).convert("L")


# -- layout ------------------------------------------------------------------
def render(spec: LabelSpec, icons: dict[str, Path] | None = None, default_font: str = "",
           area_top_mm: float = 0, area_bottom_mm: float = 0) -> tuple[Image.Image, list[str]]:
    """Render a label. Returns (1-bit image exactly as printed, warnings)."""
    warnings = []
    lines = list(spec.lines)
    while lines and not lines[-1].strip():
        lines.pop()
    margin = mm_to_dots(spec.margin_mm)
    # Printable band across the head (calibrated per label roll).
    off_top, off_bottom = mm_to_dots(area_top_mm), mm_to_dots(area_bottom_mm)
    top = off_top + margin
    avail_h = DOTS - top - off_bottom - margin
    if avail_h < 24:
        raise LabelError("Rand oder Druckbereich lassen keinen Platz auf dem Etikett.")
    mid = top + avail_h // 2
    length = mm_to_dots(spec.length_mm) if spec.length_mm else 0
    if length and length < 2 * margin + 24:
        raise LabelError("Das Etikett ist zu kurz für diesen Rand.")
    inner_w = length - 2 * margin if length else None
    gap = mm_to_dots(1.5)

    # graphic block: QR code, icon or image
    graphic = None
    g_h = int(avail_h * spec.graphic_scale)
    if spec.qr:
        graphic = qr_image(spec.qr, min(g_h, inner_w or g_h))
    elif spec.icon or spec.image:
        if spec.icon:
            path = (icons or {}).get(spec.icon)
            if not path:
                raise LabelError(f"Icon „{spec.icon}“ nicht gefunden.")
            graphic = load_image(path.read_bytes())
        else:
            graphic = load_image(decode_data_url(spec.image))
        if spec.image_rotate:
            graphic = graphic.rotate(-90 * spec.image_rotate, expand=True, fillcolor=255)
        graphic = ImageOps.contain(graphic, (inner_w or mm_to_dots(300), g_h))
        if spec.icon and path.parent == ICON_SET:    # clean edges instead of dithering
            graphic = graphic.point(lambda v: 255 if v >= 128 else 0)
    g_w = graphic.width + (gap if lines else 0) if graphic else 0

    # text block
    font, line_h, total_h, text_w = None, 0, 0, 0
    if lines:
        name = spec.font or default_font
        text_room = inner_w - g_w if inner_w else None
        if text_room is not None and text_room < 16:
            raise LabelError("Neben der Grafik ist kein Platz für Text. Etikett länger oder Grafik kleiner machen.")

        def measure(size):
            f = _font(name, spec.bold, int(size))
            asc, desc = f.getmetrics()
            lh = int((asc + desc) * spec.line_spacing)
            return f, lh, lh * (len(lines) - 1) + asc + desc, max(f.getlength(t) for t in lines)

        def fits(m):
            return m[2] <= avail_h and (text_room is None or m[3] <= text_room)

        if spec.size_pt:
            best = measure(spec.size_pt * DPI / 72)
        else:
            lo, hi = 6, avail_h
            while lo < hi:                       # largest size that fits
                size = (lo + hi + 1) // 2
                lo, hi = (size, hi) if fits(measure(size)) else (lo, size - 1)
            best = measure(lo)
        if not fits(best):
            warnings.append("Der Text passt nicht vollständig auf das Etikett.")
        font, line_h, total_h, text_w = best

    if not length:
        length = max(int(2 * margin + g_w + text_w) + mm_to_dots(2), mm_to_dots(10))
        if length > mm_to_dots(300):
            raise LabelError("Das Etikett würde länger als 300 mm.")

    img = Image.new("L", (length, DOTS), 255)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"                             # no anti-aliasing -> crisp dots

    x0, x1 = margin, length - margin
    g_box = None
    if graphic:
        gx = x1 - graphic.width if spec.graphic_pos == "right" else x0
        if not lines:
            gx = (length - graphic.width) // 2
        g_box = (gx, mid - graphic.height // 2)
        img.paste(graphic, g_box)
        if spec.graphic_pos == "right":
            x1 -= g_w
        else:
            x0 += g_w

    y = mid - total_h // 2
    for line in lines:
        w = font.getlength(line)
        x = {"left": x0, "right": x1 - w}.get(spec.align, x0 + (x1 - x0 - w) / 2)
        d.text((x, y), line, font=font, fill=0)
        y += line_h

    if spec.border:
        b = max(mm_to_dots(0.3), 2)
        d.rectangle([0, off_top, length - 1, DOTS - 1 - off_bottom], outline=0, width=b)

    if spec.invert:
        img = ImageOps.invert(img)
        d = ImageDraw.Draw(img)
        if spec.qr and graphic:                  # QR codes stay black on white
            q = gap // 2
            d.rectangle([g_box[0] - q, g_box[1] - q, g_box[0] + graphic.width + q - 1,
                         g_box[1] + graphic.height + q - 1], fill=255)
            img.paste(graphic, g_box)
        if off_top:                              # nothing on the backing paper
            d.rectangle([0, 0, length, off_top - 1], fill=255)
        if off_bottom:
            d.rectangle([0, DOTS - off_bottom, length, DOTS], fill=255)
    return img.convert("1"), warnings


def calibration_image() -> Image.Image:
    """Ruler across the print head: a line every mm, numbered every 2 mm."""
    length = mm_to_dots(50)
    img = Image.new("L", (length, DOTS), 255)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    font = _font("Liberation Sans", True, 15)
    for k in range(25):
        y = min(mm_to_dots(k), DOTS - 1)
        major = k % 2 == 0
        d.line([(0, y), (mm_to_dots(6 if major else 3), y)], fill=0, width=2 if major else 1)
        d.line([(mm_to_dots(30 if major else 36), y), (length, y)], fill=0, width=2 if major else 1)
        if major:
            ty = min(max(y, 7), DOTS - 8)        # keep 0 and 24 readable
            for x in (8, 20):
                d.text((mm_to_dots(x), ty), str(k), font=font, fill=0, anchor="lm")
    return img.convert("1")


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
