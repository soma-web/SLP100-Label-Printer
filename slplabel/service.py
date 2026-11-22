"""Application logic shared by the web UI and the MCP server."""

import json
import os
import re
import secrets
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import render
from .config import Config
from .printer import Printer
from .protocol import to_job
from .render import LabelError, LabelSpec

BUILTIN_ICONS = Path(__file__).parent / "icons"
ICON_DIRS = (BUILTIN_ICONS, render.ICON_SET)
MAX_TEMPLATES = 500
MAX_ICON_BYTES = 3_000_000


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    density: int = Field(1, ge=0, le=3)
    fine: bool = True
    default_font: str = Field("", max_length=60)
    default_length_mm: float = Field(83, ge=0, le=300)
    area_top_mm: float = Field(0, ge=0, le=10)
    area_bottom_mm: float = Field(0, ge=0, le=10)
    mcp_enabled: bool = False


def error_text(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors()[:3])
    return str(e)


class Service:
    def __init__(self, config: Config, printer: Printer | None = None):
        self.config = config
        self.dir = config.data_dir
        self.icon_dir = self.dir / "icons"
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.printer = printer or Printer(config.backend, config.usb_id, config.device)
        self.lock = threading.RLock()
        raw = self._load("settings.json", None)
        self.settings = Settings(**({"mcp_enabled": config.mcp} if raw is None else raw))
        self.mcp_token = config.mcp_token or self._stored_token()

    # -- storage -----------------------------------------------------------
    def _load(self, name, default):
        path = self.dir / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, name, data):
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / name)

    def _stored_token(self) -> str:
        path = self.dir / "mcp_token"
        if path.exists() and (tok := path.read_text().strip()):
            return tok
        tok = secrets.token_urlsafe(32)
        path.touch(mode=0o600)
        path.write_text(tok)
        return tok

    # -- settings ----------------------------------------------------------
    def update_settings(self, changes: dict) -> Settings:
        with self.lock:
            new = Settings(**{**self.settings.model_dump(), **changes})
            self._save("settings.json", new.model_dump())
            self.settings = new
            return new

    # -- templates ---------------------------------------------------------
    def templates(self) -> dict[str, dict]:
        with self.lock:
            return self._load("templates.json", {})

    @staticmethod
    def _template_name(name) -> str:
        name = str(name or "").strip()
        if not name or len(name) > 80:
            raise LabelError("Der Name muss 1 bis 80 Zeichen lang sein.")
        return name

    def get_template(self, name: str) -> LabelSpec:
        data = self.templates().get(name)
        if data is None:
            raise LabelError(f"Vorlage „{name}“ nicht gefunden.")
        return LabelSpec(**data)

    def save_templates(self, items: dict, overwrite=True) -> dict:
        """Validate and store templates; returns counts."""
        if not isinstance(items, dict):
            raise LabelError("Ungültige Vorlagendatei.")
        checked = {self._template_name(n): LabelSpec(**s).model_dump(exclude_defaults=True)
                   for n, s in items.items()}
        with self.lock:
            all_ = self.templates()
            skipped = 0
            for n, s in checked.items():
                if n in all_ and not overwrite:
                    skipped += 1
                    continue
                all_[n] = s
            if len(all_) > MAX_TEMPLATES:
                raise LabelError(f"Höchstens {MAX_TEMPLATES} Vorlagen möglich.")
            self._save("templates.json", all_)
        return {"saved": len(checked) - skipped, "skipped": skipped}

    def delete_template(self, name: str):
        with self.lock:
            all_ = self.templates()
            if all_.pop(name, None) is None:
                raise LabelError(f"Vorlage „{name}“ nicht gefunden.")
            self._save("templates.json", all_)

    # -- icons -------------------------------------------------------------
    def icons(self) -> dict[str, Path]:
        found = {}
        for d in (*ICON_DIRS, self.icon_dir):
            for p in sorted(d.glob("*.png")):
                found[p.stem] = p
        return found

    def icon_list(self) -> list[dict]:
        """Icons for choosing: own ones first, then the drawn motifs, then the set."""
        index = render.icon_index()
        out = []
        for name, path in self.icons().items():
            own = path.parent == self.icon_dir
            meta = {} if own else index.get(name, {})
            out.append({"name": name, "own": own, "label": meta.get("label") or name.capitalize(),
                        "category": "Eigene Icons" if own else meta.get("category", "Motive"),
                        "words": meta.get("words", "")})
        order = {n: i for i, n in enumerate(index)}
        out.sort(key=lambda i: (not i["own"], i["category"] != "Motive", order.get(i["name"], -1)))
        return out

    def add_icon(self, name: str, data_url: str) -> str:
        name = re.sub(r"[^\w-]+", "-", str(name or "").strip().lower()).strip("-")[:60]
        if not name:
            raise LabelError("Ungültiger Icon-Name.")
        if any((d / f"{name}.png").exists() for d in ICON_DIRS):
            raise LabelError("Ein mitgeliefertes Icon heißt schon so.")
        raw = render.decode_data_url(data_url)
        if len(raw) > MAX_ICON_BYTES:
            raise LabelError("Das Bild ist zu groß (höchstens 3 MB).")
        img = render.load_image(raw)
        img.thumbnail((800, 800))
        (self.icon_dir / f"{name}.png").write_bytes(render.png_bytes(img))
        return name

    def delete_icon(self, name: str):
        path = self.icons().get(name)
        if path is None or path.parent != self.icon_dir:
            raise LabelError("Nur eigene Icons können gelöscht werden.")
        path.unlink()

    # -- labels ------------------------------------------------------------
    def render(self, spec: LabelSpec):
        s = self.settings
        return render.render(spec, self.icons(), s.default_font, s.area_top_mm, s.area_bottom_mm)

    def print_label(self, spec: LabelSpec, copies: int = 1, density=None, fine=None, limit=None) -> dict:
        limit = limit or self.config.max_copies
        if not 1 <= int(copies) <= limit:
            raise LabelError(f"Anzahl muss zwischen 1 und {limit} liegen.")
        if not (any(t.strip() for t in spec.lines) or spec.qr or spec.icon or spec.image or spec.frame != "none"):
            raise LabelError("Das Etikett ist leer.")
        img, warnings = self.render(spec)
        s = self.settings
        job = to_job(img, s.density if density is None else density, s.fine if fine is None else fine)
        done = self.printer.print_job(job, int(copies))
        return {"copies": done, "length_mm": round(render.dots_to_mm(img.width), 1), "warnings": warnings}

    def print_calibration(self):
        self.printer.print_job(to_job(render.calibration_image(), self.settings.density, self.settings.fine))
