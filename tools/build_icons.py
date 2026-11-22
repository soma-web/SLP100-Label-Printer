"""Build slplabel/icons/set from tools/icons.txt and the Material Design Icons font.

    npm pack @mdi/font@7.4.47 && tar xzf mdi-font-7.4.47.tgz
    python tools/build_icons.py package

Only needed when the icon list changes; the results are part of the repository.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "slplabel" / "icons" / "set"
SIZE = 384            # twice the print head, so scaling down stays crisp


def slug(label: str) -> str:
    s = label.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e"}))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def draw(font, char: str) -> Image.Image:
    """Glyph cropped to a centred square, so every icon uses the full height."""
    img = Image.new("L", (1200, 1200), 255)
    ImageDraw.Draw(img).text((600, 600), char, font=font, fill=0, anchor="mm")
    x0, y0, x1, y1 = img.point(lambda v: 255 - v).getbbox()
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    img = img.crop((cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side))
    img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    return img.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)


def main():
    pkg = Path(sys.argv[1])
    codes = dict(re.findall(r'"([a-z0-9-]+)": (F[0-9A-F]+)', (pkg / "scss/_variables.scss").read_text()))
    font = ImageFont.truetype(str(pkg / "fonts/materialdesignicons-webfont.ttf"), 800)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    index, category = {}, ""
    for line in (ROOT / "tools/icons.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith("["):
            category = line.strip("[] ")
            continue
        mdi, label, words = line.split("|")
        name = slug(label)
        if name in index:
            raise SystemExit(f"duplicate icon {name}")
        draw(font, chr(int(codes[mdi], 16))).save(OUT / f"{name}.png", optimize=True)
        index[name] = {"label": label, "category": category, "words": words.strip()}
    rows = ",\n".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in index.items())
    (OUT / "index.json").write_text("{\n" + rows + "\n}\n", encoding="utf-8", newline="\n")
    print(len(index), "icons,", sum(p.stat().st_size for p in OUT.iterdir()) // 1024, "KB")


main()
