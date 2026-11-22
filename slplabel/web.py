"""Web server: UI, JSON API and the MCP endpoint (/mcp)."""

import base64
import binascii
import contextlib
import ipaddress
import json
import logging
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, render
from .mcp_server import build_mcp
from .printer import PrinterError
from .render import LabelError, LabelSpec
from .service import Service, error_text

STATIC = Path(__file__).parent / "static"
MAX_BODY = 12_000_000
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".fritz.box", ".localhost")
log = logging.getLogger("slplabel")

SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


def _host_allowed(host: str, extra: list[str]) -> bool:
    """Local names and IP addresses only; stops DNS rebinding when no password is set."""
    try:
        name = (urlsplit("//" + host).hostname or "").lower()
    except ValueError:
        return False
    if name in extra or (name and "." not in name) or name.endswith(LOCAL_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


class Guard:
    """ASGI middleware: authentication, CSRF and host checks, dispatch to MCP."""

    def __init__(self, app, service: Service, mcp_app):
        self.app, self.service, self.mcp_app = app, service, mcp_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path = scope["path"]
        if path == "/mcp":
            deny = self._check_mcp(headers)
            return await (deny or self.mcp_app)(scope, receive, send)
        if path != "/healthz":
            deny = self._check_web(scope["method"], headers)
            if deny:
                return await deny(scope, receive, send)

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg["headers"], *SECURITY_HEADERS]}
            await send(msg)

        await self.app(scope, receive, send_with_headers)

    def _check_mcp(self, headers):
        if not self.service.settings.mcp_enabled:
            return PlainTextResponse("MCP is disabled", 404)
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
                token.strip().encode(), self.service.mcp_token.encode()):
            return PlainTextResponse("Unauthorized", 401, {"WWW-Authenticate": "Bearer"})
        return None

    def _check_web(self, method, headers):
        cfg = self.service.config
        host = headers.get("host", "")
        if cfg.password:
            if not self._basic_ok(headers.get("authorization", "")):
                return PlainTextResponse("Anmeldung erforderlich", 401,
                                         {"WWW-Authenticate": 'Basic realm="SLP Label Studio", charset="UTF-8"'})
        elif not _host_allowed(host, cfg.allowed_hosts):
            return PlainTextResponse("Unbekannter Hostname. SLP_ALLOWED_HOSTS oder SLP_PASSWORD setzen.", 403)
        if method not in ("GET", "HEAD"):
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() != host.lower():
                return PlainTextResponse("Fremde Herkunft", 403)
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return PlainTextResponse("application/json erwartet", 415)
        return None

    def _basic_ok(self, value: str) -> bool:
        scheme, _, data = value.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            user, _, pw = base64.b64decode(data.strip(), validate=True).partition(b":")
        except (binascii.Error, ValueError):
            return False
        cfg = self.service.config
        ok_user = secrets.compare_digest(user, cfg.username.encode())
        ok_pw = secrets.compare_digest(pw, cfg.password.encode())
        return ok_user and ok_pw


async def _json(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise LabelError("Die Anfrage ist zu groß.")
    try:
        data = json.loads(body or b"{}")
    except ValueError as e:
        raise LabelError("Ungültiges JSON.") from e
    if not isinstance(data, dict):
        raise LabelError("JSON-Objekt erwartet.")
    return data


def create_app(service: Service) -> Guard:
    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True, json_response=True,
        # every request needs the bearer token, so rebinding protection adds nothing
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    def spec_of(body) -> LabelSpec:
        label = body.get("label")
        if not isinstance(label, dict):
            raise LabelError("Etikett fehlt.")
        return LabelSpec(**label)

    def api(fn):
        """Run a blocking handler in a thread and map errors to JSON."""
        async def endpoint(request: Request):
            try:
                body = await _json(request) if request.method in ("POST", "DELETE") else {}
                result = await run_in_threadpool(fn, request, body)
            except PrinterError as e:
                return JSONResponse({"error": str(e)}, 503)
            except (LabelError, ValidationError) as e:
                return JSONResponse({"error": error_text(e)}, 400)
            except Exception:
                log.exception("request failed")
                return JSONResponse({"error": "Interner Fehler, Details stehen im Log."}, 500)
            return result if isinstance(result, Response) else JSONResponse(result)
        return endpoint

    def info(_r, _b):
        return {"version": __version__, "fonts": render.available_fonts(),
                "media": [{"name": n, "width": w, "length": ln} for n, w, ln in render.MEDIA],
                "icons": [{"name": n, "own": p.parent == service.icon_dir} for n, p in service.icons().items()],
                "settings": service.settings.model_dump(), "max_copies": service.config.max_copies,
                "password_set": bool(service.config.password)}

    def preview(_r, body):
        img, warnings = service.render(spec_of(body))
        return {"png": base64.b64encode(render.png_bytes(img)).decode(),
                "length_mm": round(render.dots_to_mm(img.width), 1), "warnings": warnings}

    def print_(_r, body):
        copies = body.get("copies", 1)
        if not isinstance(copies, int):
            raise LabelError("Ungültige Anzahl.")
        return service.print_label(spec_of(body), copies)

    def feed(_r, _b):
        service.printer.feed()
        return {"ok": True}

    def calibrate(_r, _b):
        service.print_calibration()
        return {"ok": True}

    def save_settings(_r, body):
        return service.update_settings(body).model_dump()

    def save_template(_r, body):
        return service.save_templates({body.get("name"): body.get("label") or {}})

    def import_templates(_r, body):
        return service.save_templates(body.get("templates"), overwrite=bool(body.get("overwrite")))

    def delete_template(r, _b):
        service.delete_template(r.query_params.get("name", ""))
        return {"ok": True}

    def icon_file(r, _b):
        path = service.icons().get(r.path_params["name"])
        return FileResponse(path, media_type="image/png") if path else JSONResponse({"error": "Nicht gefunden"}, 404)

    def add_icon(_r, body):
        return {"name": service.add_icon(body.get("name"), str(body.get("image") or ""))}

    def delete_icon(r, _b):
        service.delete_icon(r.path_params["name"])
        return {"ok": True}

    def mcp_info(_r, _b):
        return {"enabled": service.settings.mcp_enabled, "token": service.mcp_token}

    routes = [
        Route("/", lambda _r: FileResponse(STATIC / "index.html")),
        Route("/healthz", lambda _r: PlainTextResponse("ok")),
        Route("/api/info", api(info)),
        Route("/api/status", api(lambda _r, _b: service.printer.status())),
        Route("/api/preview", api(preview), methods=["POST"]),
        Route("/api/print", api(print_), methods=["POST"]),
        Route("/api/feed", api(feed), methods=["POST"]),
        Route("/api/calibrate", api(calibrate), methods=["POST"]),
        Route("/api/settings", api(save_settings), methods=["POST"]),
        Route("/api/templates", api(lambda _r, _b: service.templates())),
        Route("/api/templates", api(save_template), methods=["POST"]),
        Route("/api/templates", api(delete_template), methods=["DELETE"]),
        Route("/api/templates/import", api(import_templates), methods=["POST"]),
        Route("/api/icons", api(add_icon), methods=["POST"]),
        Route("/api/icons/{name}", api(icon_file)),
        Route("/api/icons/{name}", api(delete_icon), methods=["DELETE"]),
        Route("/api/mcp", api(mcp_info)),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]
    return Guard(Starlette(routes=routes, lifespan=lifespan), service, mcp_app)
