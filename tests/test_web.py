import asyncio
import contextlib
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from slplabel.config import Config
from slplabel.service import Service
from slplabel.web import _host_allowed, create_app


@contextlib.contextmanager
def running(tmp_path, **cfg):
    service = Service(Config(data_dir=tmp_path, backend="mock", **cfg))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(service), log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    try:
        yield service, f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        t.join(10)


@pytest.fixture
def web(tmp_path):
    with running(tmp_path) as (service, url), httpx.Client(base_url=url) as client:
        yield service, client


LABEL = {"lines": ["Hallo"], "length_mm": 83}


def test_pages_and_headers(web):
    _, c = web
    for path in ("/", "/static/app.js", "/static/style.css", "/api/icons/labor", "/healthz", "/api/status"):
        assert c.get(path).status_code == 200, path
    assert "default-src 'self'" in c.get("/").headers["content-security-policy"]
    assert c.get("/static/../web.py").status_code == 404
    assert c.get("/static/%2e%2e/web.py").status_code == 404
    assert c.get("/api/icons/%2e%2e").status_code == 404


def test_preview_print_templates(web):
    service, c = web
    r = c.post("/api/preview", json={"label": LABEL}).json()
    assert r["length_mm"] == 83.0 and r["png"]
    assert c.post("/api/print", json={"label": LABEL, "copies": 2}).json()["copies"] == 2
    assert len(service.printer.mock_labels) == 2
    assert c.post("/api/templates", json={"name": "T <b>", "label": LABEL}).status_code == 200
    assert "T <b>" in c.get("/api/templates").json()
    assert c.request("DELETE", "/api/templates?name=T <b>", json={}).status_code == 200
    assert c.get("/api/templates").json() == {}


def test_errors_are_clean(web):
    service, c = web
    assert c.post("/api/print", json={"label": {"lines": []}}).status_code == 400
    assert c.post("/api/print", json={"label": LABEL, "copies": "x"}).status_code == 400
    assert c.post("/api/preview", json={}).status_code == 400
    assert c.post("/api/preview", content="{", headers={"Content-Type": "application/json"}).status_code == 400
    assert c.post("/api/settings", json={"density": 99}).status_code == 400
    assert c.post("/api/templates/import", json={"templates": [1]}).status_code == 400
    service.printer.mock_status = 0x38
    r = c.post("/api/print", json={"label": LABEL})
    assert r.status_code == 503 and "Etiketten" in r.json()["error"]


def test_cross_site_requests_are_blocked(web):
    service, c = web
    body = '{"label":{"lines":["x"]}}'
    assert c.post("/api/print", content=body, headers={"Content-Type": "text/plain"}).status_code == 415
    assert c.post("/api/print", json={"label": LABEL}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.get("/api/mcp", headers={"Host": "evil.example"}).status_code == 403
    assert service.printer.mock_labels == []


@pytest.mark.parametrize("host,ok", [
    ("192.168.1.5:8100", True), ("[::1]:8100", True), ("localhost", True), ("mein-server", True),
    ("mein-server.fritz.box:8100", True), ("evil.example", False), ("", False), ("my.domain", True),
    ("fritz.box.evil.example", False), ("[bad", False)])
def test_host_check(host, ok):
    assert _host_allowed(host, ["my.domain"]) is ok


def test_password(tmp_path):
    with running(tmp_path, password="geheim", username="ich") as (_, url), httpx.Client(base_url=url) as c:
        assert c.get("/").status_code == 401
        assert c.get("/api/mcp").status_code == 401
        assert c.post("/api/print", json={"label": LABEL}).status_code == 401
        assert c.get("/", auth=("ich", "falsch")).status_code == 401
        assert c.get("/", headers={"Authorization": "Basic %%%"}).status_code == 401
        assert c.get("/", auth=("ich", "geheim")).status_code == 200
        assert c.get("/healthz").status_code == 200


def test_mcp_is_off_and_needs_token(web):
    service, c = web
    assert c.post("/mcp", json={}).status_code == 404
    c.post("/api/settings", json={"mcp_enabled": True})
    assert c.post("/mcp", json={}).status_code == 401
    assert c.post("/mcp", json={}, headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert c.get("/api/mcp").json()["token"] == service.mcp_token


def test_mcp_tools_end_to_end(tmp_path):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def session(url, token):
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(url + "/mcp", http_client=http)) as c:
                tools = {t.name for t in (await c.list_tools()).tools}
                label = {"lines": ["Mein", "Test"], "icon": "labor", "frame": "stars"}
                preview = await c.call_tool("preview_label", label)
                before = len(service.printer.mock_labels)
                printed = await c.call_tool("print_label", {**label, "copies": 2})
                many = await c.call_tool("print_label", {**label, "copies": 11})
                await c.call_tool("save_template", {"name": "Neu", **label})
                tpl = await c.call_tool("print_template", {"name": "Neu"})
                status = await c.call_tool("get_printer_status", {})
                return tools, preview, before, printed, many, tpl, status

    with running(tmp_path, mcp=True) as (service, url):
        tools, preview, before, printed, many, tpl, status = asyncio.run(session(url, service.mcp_token))
    assert {"preview_label", "print_label", "print_template", "get_printer_status"} <= tools
    assert [x.type for x in preview.content] == ["text", "image"]
    assert before == 0, "preview must not print"
    assert not printed.is_error and not tpl.is_error and not status.is_error
    assert many.is_error
    assert len(service.printer.mock_labels) == 3
