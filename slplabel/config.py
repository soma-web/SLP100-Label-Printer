"""Configuration from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on", "ja")


@dataclass
class Config:
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8100
    username: str = "admin"
    password: str = ""
    backend: str = "auto"
    usb_id: str = "0619:0103"
    device: str = "/dev/usb/lp0"
    mcp: bool = False                 # initial state, can be toggled in the UI
    mcp_token: str = ""               # empty = generated and stored in data_dir
    max_copies: int = 50
    mcp_max_copies: int = 10
    allowed_hosts: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        c = cls()
        c.data_dir = Path(env.get("SLP_DATA") or "data")
        c.host = env.get("SLP_HOST") or c.host
        c.port = int(env.get("SLP_PORT") or c.port)
        c.username = env.get("SLP_USERNAME") or c.username
        c.password = env.get("SLP_PASSWORD") or ""
        c.backend = env.get("SLP_BACKEND") or c.backend
        c.usb_id = env.get("SLP_USB_ID") or c.usb_id
        c.device = env.get("SLP_DEVICE") or c.device
        c.mcp = _bool(env.get("SLP_MCP") or "")
        c.mcp_token = env.get("SLP_MCP_TOKEN") or ""
        c.mcp_max_copies = int(env.get("SLP_MCP_MAX_COPIES") or c.mcp_max_copies)
        c.allowed_hosts = [h.strip().lower() for h in (env.get("SLP_ALLOWED_HOSTS") or "").split(",") if h.strip()]
        return c
