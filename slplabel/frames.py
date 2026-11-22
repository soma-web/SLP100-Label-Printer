"""Frame styles. Drawn enlarged and reduced to pure black/white for clean edges."""

import math
from functools import lru_cache

from PIL import Image, ImageDraw

from .protocol import DPI

SCALE = 4                    # enlargement while drawing
MM = DPI / 25.4 * SCALE      # pixels per mm on the enlarged drawing


def _px(mm: float) -> int:
    return round(mm * DPI / 25.4) * SCALE


class _Pen:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)

    def box(self, inset, fill=None, width=0, radius=0):
        i = round(inset)
        xy = [i, i, self.w - 1 - i, self.h - 1 - i]
        if xy[2] <= xy[0] or xy[3] <= xy[1]:
            return
        radius = min(radius, (xy[2] - xy[0]) // 2, (xy[3] - xy[1]) // 2)
        self.d.rounded_rectangle(xy, max(radius - i, 0) if radius else 0, fill=fill,
                                 outline=0 if width else None, width=round(width) or 1)

    def disc(self, x, y, r, fill=0):
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def around(self, inset, step, even=False):
        """Evenly spaced points along the edge, always including the corners."""
        x0, y0, x1, y1 = inset, inset, self.w - 1 - inset, self.h - 1 - inset
        nx, ny = max(1, round((x1 - x0) / step)), max(1, round((y1 - y0) / step))
        if even:
            nx, ny = nx + nx % 2, ny + ny % 2
        for k in range(nx + 1):
            x = x0 + (x1 - x0) * k / nx
            yield x, y0
            yield x, y1
        for k in range(1, ny):
            y = y0 + (y1 - y0) * k / ny
            yield x0, y
            yield x1, y


def _line(p):
    p.box(0, width=_px(0.3))


def _thick(p):
    p.box(0, width=_px(1))


def _double(p):
    p.box(0, width=_px(0.3))
    p.box(_px(0.8), width=_px(0.3))


def _thick_thin(p):
    p.box(0, width=_px(0.8))
    p.box(_px(1.2), width=_px(0.25))


def _rounded(p):
    p.box(0, width=_px(0.5), radius=_px(3))


def _rounded_double(p):
    p.box(0, width=_px(0.4), radius=_px(3))
    p.box(_px(0.9), width=_px(0.25), radius=_px(3))


def _dashes(p, length, gap, width):
    for size, horizontal in ((p.w, True), (p.h, False)):
        n = max(2, round((size + gap) / (length + gap)))
        for k in range(n):
            a = round((size - length) * k / (n - 1))
            b = a + length - 1
            for xy in (([a, 0, b, width - 1], [a, p.h - width, b, p.h - 1]) if horizontal
                       else ([0, a, width - 1, b], [p.w - width, a, p.w - 1, b])):
                p.d.rectangle(xy, fill=0)


def _dashed(p):
    _dashes(p, _px(2), _px(1.2), _px(0.4))


def _dotted(p):
    r = _px(0.4)
    for x, y in p.around(r, _px(1.6)):
        p.disc(x, y, r)


def _corners(p):
    n, w = min(_px(6), p.w // 3, p.h // 3), _px(0.5)
    for sx in (0, 1):
        for sy in (0, 1):
            x, y = sx * (p.w - 1), sy * (p.h - 1)
            for dx, dy in ((n, w), (w, n)):
                x2, y2 = x + (dx - 1) * (1 - 2 * sx), y + (dy - 1) * (1 - 2 * sy)
                p.d.rectangle([min(x, x2), min(y, y2), max(x, x2), max(y, y2)], fill=0)


def _crossed(p):
    i, w = _px(0.9), _px(0.4)
    p.d.rectangle([0, i, p.w - 1, i + w - 1], fill=0)
    p.d.rectangle([0, p.h - i - w, p.w - 1, p.h - 1 - i], fill=0)
    p.d.rectangle([i, 0, i + w - 1, p.h - 1], fill=0)
    p.d.rectangle([p.w - i - w, 0, p.w - 1 - i, p.h - 1], fill=0)


def _notched(p):
    r, w = _px(2.5), _px(0.4)
    for fill, inset, radius in ((0, 0, r), (255, w, r + w)):
        shape = _Pen(p.w, p.h)
        shape.box(inset, fill=0)
        for x in (0, p.w - 1):
            for y in (0, p.h - 1):
                shape.disc(x, y, radius, 255)
        p.img.paste(fill, (0, 0), shape.img.point(lambda v: 255 - v))


def _cut(p):
    c, w = _px(2.5), _px(0.45)
    for fill, i in ((0, 0), (255, w)):
        a = c + i * 0.414               # keeps the diagonal as thick as the sides
        x1, y1 = p.w - 1, p.h - 1
        p.d.polygon([(a, i), (x1 - a, i), (x1 - i, a), (x1 - i, y1 - a), (x1 - a, y1 - i),
                     (a, y1 - i), (i, y1 - a), (i, a)], fill=fill)


def _shadow(p):
    s, w = _px(0.9), _px(0.3)
    p.d.rectangle([s, s, p.w - 1, p.h - 1], fill=0)
    p.d.rectangle([0, 0, p.w - 1 - s, p.h - 1 - s], fill=255, outline=0, width=w)


def _deco(p):
    p.box(0, width=_px(0.25))
    p.box(_px(0.8), width=_px(0.25))
    n = _px(1.6)
    for x in (0, p.w - n):
        for y in (0, p.h - n):
            p.d.rectangle([x, y, x + n - 1, y + n - 1], fill=0)


def _cloud(p):
    r, w = _px(1.1), _px(0.35)
    points = list(p.around(r, _px(1.9)))
    for fill, shrink in ((0, 0), (255, w)):
        for x, y in points:
            p.disc(x, y, r - shrink, fill)
        if fill == 255:
            p.box(r, fill=255)


def _zigzag(p):
    t, w = _px(1.1), _px(0.35)
    for size, horizontal in ((p.w, True), (p.h, False)):
        a, b = t, size - 1 - t
        n = max(1, round((b - a) / _px(1.8)))
        for k in range(n):
            u, v = a + (b - a) * k / n, a + (b - a) * (k + 1) / n
            far = (p.h if horizontal else p.w) - 1
            for base, tip in ((t, 0), (far - t, far)):
                tri = [(u, base), ((u + v) / 2, tip), (v, base)]
                p.d.polygon(tri if horizontal else [(y, x) for x, y in tri], fill=0)
    p.box(t, fill=255, width=w)


def _stamp(p):
    p.box(0, fill=0)
    for x, y in p.around(0, _px(1.9)):
        p.disc(x, y, _px(0.55), 255)
    p.box(_px(1.2), fill=255)


def _film(p):
    band, hole_w, hole_h = _px(3), _px(1.6), _px(1.4)
    n = max(1, round(p.w / _px(3)))
    for y in (0, p.h - band):
        p.d.rectangle([0, y, p.w - 1, y + band - 1], fill=0)
        for k in range(n):
            cx, cy = p.w * (k + 0.5) / n, y + band / 2
            p.d.rounded_rectangle([cx - hole_w / 2, cy - hole_h / 2, cx + hole_w / 2, cy + hole_h / 2],
                                  _px(0.3), fill=255)


def _hazard(p):
    band, w, step = _px(2), _px(0.25), _px(2.6)
    for x in range(-p.h - step, p.w + step, step):
        p.d.polygon([(x, p.h), (x + step // 2, p.h), (x + step // 2 + p.h, 0), (x + p.h, 0)], fill=0)
    p.box(0, width=w)
    p.box(band, fill=255, width=w)


def _checker(p):
    cell = _px(0.8)
    nx, ny = (max(5, round(s / cell)) // 2 * 2 + 1 for s in (p.w, p.h))
    for i in range(nx):
        for j in range(ny):
            if (i + j) % 2 == 0 and (min(i, nx - 1 - i) < 2 or min(j, ny - 1 - j) < 2):
                p.d.rectangle([round(i * p.w / nx), round(j * p.h / ny),
                               round((i + 1) * p.w / nx) - 1, round((j + 1) * p.h / ny) - 1], fill=0)


def _stars(p):
    r = _px(1.1)
    for x, y in p.around(r, _px(3)):
        p.d.polygon([(x + math.sin(k * math.pi / 5) * (r if k % 2 == 0 else r * 0.45),
                      y - math.cos(k * math.pi / 5) * (r if k % 2 == 0 else r * 0.45))
                     for k in range(10)], fill=0)


def _hearts(p):
    r = _px(1.1)
    for x, y in p.around(r, _px(3)):
        points = []
        for k in range(40):
            t = k * math.pi / 20
            points.append((x + r * math.sin(t) ** 3,
                           y - r / 16 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t)
                                         - math.cos(4 * t))))
        p.d.polygon(points, fill=0)


def _bubbles(p):
    for k, (x, y) in enumerate(p.around(_px(1), _px(2.4), even=True)):
        # big and small take turns; even counts put big ones in the corners
        big = (k // 2) % 2 == 0
        r = _px(0.95) if big else _px(0.55)
        p.disc(x, y, r)
        if big:
            p.disc(x, y, r - _px(0.3), 255)


# key -> (German name, drawing, depth left/right in mm, depth top/bottom in mm)
FRAMES = {
    "line": ("Linie", _line, 0.3, 0.3),
    "thick": ("Dick", _thick, 1, 1),
    "double": ("Doppelt", _double, 1.1, 1.1),
    "thick-thin": ("Dick & dünn", _thick_thin, 1.45, 1.45),
    "rounded": ("Abgerundet", _rounded, 0.5, 0.5),
    "rounded-double": ("Rund doppelt", _rounded_double, 1.2, 1.2),
    "dashed": ("Gestrichelt", _dashed, 0.4, 0.4),
    "dotted": ("Gepunktet", _dotted, 0.8, 0.8),
    "corners": ("Eckwinkel", _corners, 0.5, 0.5),
    "crossed": ("Gekreuzt", _crossed, 1.3, 1.3),
    "notched": ("Nostalgie", _notched, 2.3, 0.5),
    "cut": ("Schräge Ecken", _cut, 1.9, 0.5),
    "deco": ("Art déco", _deco, 1.2, 1.2),
    "shadow": ("Schatten", _shadow, 1, 1),
    "stamp": ("Briefmarke", _stamp, 1.2, 1.2),
    "cloud": ("Wolke", _cloud, 1.4, 1.4),
    "zigzag": ("Zacken", _zigzag, 1.5, 1.5),
    "checker": ("Schachbrett", _checker, 1.7, 1.7),
    "hazard": ("Warnstreifen", _hazard, 2.3, 2.3),
    "film": ("Filmstreifen", _film, 0, 3),
    "stars": ("Sterne", _stars, 2.2, 2.2),
    "hearts": ("Herzen", _hearts, 2.2, 2.2),
    "bubbles": ("Blasen", _bubbles, 2, 2),
}


@lru_cache(maxsize=64)
def frame_image(style: str, w: int, h: int) -> Image.Image:
    """Frame of w x h dots, black on white, values 0 and 255 only."""
    pen = _Pen(w * SCALE, h * SCALE)
    FRAMES[style][1](pen)
    small = pen.img.resize((w, h), Image.Resampling.BOX)
    return small.point(lambda v: 255 if v >= 128 else 0)
