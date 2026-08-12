"""HTTP smoke tests (no Ollama required)."""
from __future__ import annotations
from fastapi.testclient import TestClient

from backend.app import app
from backend import config


def test_pages_and_collections():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        dash = c.get("/dashboard")
        assert dash.status_code == 200
        assert b"new URLSearchParams" in dash.content
        scan = c.get("/scan")
        assert scan.status_code == 200
        assert b"requestAnimationFrame(detectLoop)" in scan.content
        assert c.get("/api/collections").status_code == 401
        r = c.get("/api/collections", headers={"X-Coinscope-Token": config.API_TOKEN})
        assert r.status_code == 200
        assert "years" in r.json()
