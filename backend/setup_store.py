"""Persisted first-run setup: one-computer vs two-computer Ollama routing."""
from __future__ import annotations
import json
import socket
from pathlib import Path
from typing import Any

from . import config

SETUP_PATH = config.DATA_DIR / "setup.json"

_DEFAULTS: dict[str, Any] = {
    "completed": False,
    "mode": "two-computer",  # 'one-computer' | 'two-computer'
    "ollama_host": "",
    "ollama_port": 11434,
    "model": config.DEFAULT_VISION_MODEL,
    "fastapi_port": config.PORT,
}


def _read() -> dict[str, Any]:
    if not SETUP_PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
        out = dict(_DEFAULTS)
        out.update(data)
        return out
    except Exception:
        return dict(_DEFAULTS)


def load() -> dict[str, Any]:
    return _read()


def save(update: dict[str, Any]) -> dict[str, Any]:
    current = _read()
    for k, v in update.items():
        if k in _DEFAULTS or k in current:
            current[k] = v
    current["completed"] = True
    SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETUP_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    apply_to_config(current)
    return current


def apply_to_config(data: dict[str, Any] | None = None) -> None:
    """Push setup.json into live config (env vars still win)."""
    data = data or _read()
    import os

    if not os.environ.get("OLLAMA_BASE"):
        host = (data.get("ollama_host") or "").strip()
        port = int(data.get("ollama_port") or 11434)
        mode = data.get("mode") or "two-computer"
        if mode == "one-computer" or not host:
            config.OLLAMA_BASE = "http://127.0.0.1:11434"
        else:
            if host.startswith("http://") or host.startswith("https://"):
                config.OLLAMA_BASE = host.rstrip("/")
            else:
                config.OLLAMA_BASE = f"http://{host}:{port}"
    if not os.environ.get("COINSCOPE_MODEL") and data.get("model"):
        config.OLLAMA_MODEL = str(data["model"])


def lan_ipv4_addresses() -> list[str]:
    """Best-effort private IPv4 addresses the phone can use to reach this Mac."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        found.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except Exception:
        pass
    private = []
    for ip in found:
        if ip.startswith("127."):
            continue
        private.append(ip)
    return private or found
