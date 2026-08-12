"""FastAPI app. Serves the scanner page, dashboard page, photo upload,
scan listing, setup wizard, and WebSocket live updates.
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from . import db
from . import jobs
from . import ollama_client
from . import setup_store
from .canonical import export_canonical, rarity_flag

logging.basicConfig(
    level=os.environ.get("COINSCOPE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("coinscope")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    setup_store.apply_to_config()
    jobs.set_event_loop(asyncio.get_running_loop())
    log.info(
        "Coinscope started. Ollama: %s, model: %s, photos: %s",
        config.OLLAMA_BASE,
        config.OLLAMA_MODEL,
        config.PHOTOS_DIR,
    )
    yield


app = FastAPI(title="Coinscope", version="0.2.0", lifespan=_lifespan)

# Phone APK is a local WebView calling this host over LAN HTTP.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _check_token(request: Request) -> None:
    token = request.headers.get("x-coinscope-token") or request.query_params.get("token")
    if token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


def _enrich(scan: dict[str, Any]) -> dict[str, Any]:
    """Add collection year + rarity flag for the dashboard."""
    ident = scan.get("identification") or {}
    rar = scan.get("rarity") or {}
    year = str(ident.get("year") or "").strip()
    scan["collection_year"] = year if year.isdigit() and len(year) == 4 else "Unknown"
    scan["rarity_flag"] = rar.get("flag") or rarity_flag(rar.get("rarity_tier"))
    return scan


@app.get("/", response_class=HTMLResponse)
async def root() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/scan", response_class=HTMLResponse)
async def scanner_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "scanner.html"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    setup = setup_store.load()
    return {
        "ok": True,
        "ollama": ollama_client.is_ollama_alive(),
        "ollama_base": config.OLLAMA_BASE,
        "model": config.OLLAMA_MODEL,
        "canonical_enabled": config.USE_CANONICAL_SHORTCUT,
        "setup_completed": bool(setup.get("completed")),
        "mode": setup.get("mode"),
        "lan_ips": setup_store.lan_ipv4_addresses(),
        "port": config.PORT,
        "queue_hint": "Uploads return immediately; GPU worker processes one coin at a time.",
    }


@app.get("/api/setup")
async def get_setup() -> dict[str, Any]:
    data = setup_store.load()
    data["lan_ips"] = setup_store.lan_ipv4_addresses()
    data["port"] = config.PORT
    data["ollama_base_live"] = config.OLLAMA_BASE
    data["model_live"] = config.OLLAMA_MODEL
    data["default_model"] = config.DEFAULT_VISION_MODEL
    data["ollama_alive"] = ollama_client.is_ollama_alive()
    data["models"] = ollama_client.list_models() if data["ollama_alive"] else []
    return data


@app.post("/api/setup")
async def post_setup(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Save one-computer vs two-computer routing. Called from the Mac dashboard wizard."""
    _check_token(request)
    allowed = {k: body[k] for k in ("mode", "ollama_host", "ollama_port", "model", "fastapi_port") if k in body}
    saved = setup_store.save(allowed)
    saved["lan_ips"] = setup_store.lan_ipv4_addresses()
    saved["ollama_base_live"] = config.OLLAMA_BASE
    saved["model_live"] = config.OLLAMA_MODEL
    saved["ollama_alive"] = ollama_client.is_ollama_alive()
    saved["models"] = ollama_client.list_models() if saved["ollama_alive"] else []
    return saved


@app.post("/api/setup/test-ollama")
async def test_ollama(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe an Ollama host without saving. Used by the setup wizard."""
    _check_token(request)
    body = body or {}
    host = (body.get("ollama_host") or "").strip()
    port = int(body.get("ollama_port") or 11434)
    if host:
        if host.startswith("http://") or host.startswith("https://"):
            base = host.rstrip("/")
        else:
            base = f"http://{host}:{port}"
    else:
        base = "http://127.0.0.1:11434"
    import httpx
    try:
        r = httpx.get(f"{base}/api/tags", timeout=4.0)
        if r.status_code != 200:
            return {"ok": False, "base": base, "error": f"HTTP {r.status_code}"}
        names = [m.get("name", "") for m in r.json().get("models", [])]
        wanted = body.get("model") or config.DEFAULT_VISION_MODEL
        return {
            "ok": True,
            "base": base,
            "models": names,
            "has_model": any(wanted in n or n.startswith(wanted.split(":")[0]) for n in names),
            "wanted": wanted,
        }
    except Exception as e:
        return {"ok": False, "base": base, "error": str(e)}


@app.get("/api/canonical")
async def canonical_list() -> dict[str, Any]:
    return export_canonical()


@app.post("/api/upload")
async def upload_photo(
    request: Request,
    side: str = Form("unknown"),
    side_index: int = Form(1),
    group_id: str | None = Form(None),
    notes: str | None = Form(None),
    image: UploadFile = File(...),
) -> JSONResponse:
    _check_token(request)

    if not group_id:
        group_id = jobs.new_group_id()

    if side == "obverse" and db.has_recent_front_upload(group_id, within_seconds=3):
        raise HTTPException(
            status_code=409,
            detail="Front photo already uploaded for this coin — wait 3s before next capture",
        )

    image_bytes = await image.read()
    if len(image_bytes) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image too large (max {config.MAX_UPLOAD_MB} MB)")
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception as e:
        raise HTTPException(400, f"Invalid image: {e}")

    safe_name = (image.filename or "photo.jpg").replace("/", "_").replace("\\", "_")
    scan_dir = config.PHOTOS_DIR / group_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    photo_path = scan_dir / f"{side_index}_{side}_{safe_name}"
    photo_path.write_bytes(image_bytes)

    if side_index == 2:
        pending = db.find_pending_reverse(group_id)
        if pending:
            db.set_photo_paths(pending["id"], front=None, reverse=str(photo_path))
            jobs.enqueue_scan(pending["id"], group_id)
            return JSONResponse({
                "ok": True,
                "scan_id": pending["id"],
                "group_id": group_id,
                "attached_to": "existing",
                "queued": True,
                "photo_path": str(photo_path),
            })

    scan_id = db.create_scan(group_id=group_id, side=side, side_index=side_index, notes=notes)
    if side == "reverse":
        db.set_photo_paths(scan_id, front=None, reverse=str(photo_path))
    else:
        db.set_photo_paths(scan_id, front=str(photo_path), reverse=None)
    jobs.enqueue_scan(scan_id, group_id)
    return JSONResponse({
        "ok": True,
        "scan_id": scan_id,
        "group_id": group_id,
        "attached_to": "new",
        "queued": True,
        "photo_path": str(photo_path),
    })


@app.get("/api/scans")
async def api_scans(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    _check_token(request)
    rows = [_enrich(s) for s in db.list_scans(limit=limit, offset=offset)]
    return {"scans": rows}


@app.get("/api/collections")
async def api_collections(request: Request) -> dict[str, Any]:
    """Coins grouped by year for the dashboard collections view."""
    _check_token(request)
    rows = [_enrich(s) for s in db.list_scans(limit=10000)]
    groups: dict[str, list] = {}
    for s in rows:
        year = s.get("collection_year") or "Unknown"
        groups.setdefault(year, []).append(s)
    years = sorted((y for y in groups if y != "Unknown"), reverse=True)
    if "Unknown" in groups:
        years.append("Unknown")
    return {
        "years": years,
        "collections": {y: groups[y] for y in years},
        "total": len(rows),
    }


@app.get("/api/scans/search")
async def api_search_scans(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(100, le=500),
) -> dict[str, Any]:
    _check_token(request)
    rows = [_enrich(s) for s in db.search_scans(q, limit=limit)]
    return {"scans": rows, "query": q}


@app.get("/api/scans/export.csv")
async def api_export_csv(request: Request) -> Response:
    _check_token(request)
    import csv
    import io
    scans = db.list_scans(limit=10000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "created_at", "status", "country", "denomination", "series",
        "year", "mint_mark", "rarity_tier", "rarity_flag", "estimated_value_low_usd",
        "estimated_value_high_usd", "condition_band", "condition_score",
        "condition_confidence", "condition_warnings", "group_id", "notes",
        "front_photo", "reverse_photo", "scarcity_note",
    ])
    for s in scans:
        ident = s.get("identification") or {}
        rar = s.get("rarity") or {}
        cond = s.get("condition") or {}
        w.writerow([
            s.get("id"),
            s.get("created_at"),
            s.get("status"),
            ident.get("country"),
            ident.get("denomination"),
            ident.get("series"),
            ident.get("year"),
            ident.get("mint_mark"),
            rar.get("rarity_tier"),
            rar.get("flag") or rarity_flag(rar.get("rarity_tier")),
            rar.get("estimated_value_low_usd"),
            rar.get("estimated_value_high_usd"),
            cond.get("band"),
            cond.get("score"),
            cond.get("confidence"),
            "; ".join(cond.get("warnings") or []),
            s.get("group_id"),
            s.get("notes"),
            s.get("front_photo_path"),
            s.get("reverse_photo_path"),
            rar.get("scarcity_note"),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="coinscope-scans.csv"'},
    )


@app.get("/api/scans/export.txt")
async def api_export_txt(request: Request) -> Response:
    _check_token(request)
    scans = db.list_scans(limit=10000)
    lines = [f"COINSCOPE REPORT — {len(scans)} scans", "=" * 60, ""]
    for s in scans:
        ident = s.get("identification") or {}
        rar = s.get("rarity") or {}
        cond = s.get("condition") or {}
        title = rar.get("name") or ident.get("series") or "Unknown coin"
        sub = " · ".join(filter(None, [
            ident.get("country"), ident.get("denomination"),
            ident.get("year"), ident.get("mint_mark") and ("mint " + ident["mint_mark"])
        ]))
        lines.append(f"#{s['id']}  {title}")
        if sub:
            lines.append(f"     {sub}")
        flag = rar.get("flag") or rarity_flag(rar.get("rarity_tier"))
        lines.append(f"     Rare? {flag}  ·  tier {rar.get('rarity_tier', '?')}  ·  Mintage: {rar.get('mintage', '?')}")
        lo = rar.get("estimated_value_low_usd")
        hi = rar.get("estimated_value_high_usd")
        if lo is not None or hi is not None:
            v = f"${lo:.2f} – ${hi:.2f}" if lo is not None and hi is not None else (
                f"≥ ${lo:.2f}" if hi is None else f"≤ ${hi:.2f}")
            lines.append(f"     Estimated value: {v}")
        if cond.get("band"):
            lines.append(f"     Condition: {cond['band']}  (score {cond.get('score')}, confidence {cond.get('confidence')})")
        if s.get("notes"):
            lines.append(f"     Notes: {s['notes']}")
        lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="coinscope-report.txt"'},
    )


@app.get("/api/scans/{scan_id}")
async def api_get_scan(request: Request, scan_id: int) -> dict[str, Any]:
    _check_token(request)
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404, "scan not found")
    return _enrich(scan)


@app.get("/api/photo")
async def api_photo(request: Request, path: str) -> FileResponse:
    _check_token(request)
    target = Path(path).resolve()
    if not str(target).startswith(str(config.PHOTOS_DIR.resolve())):
        raise HTTPException(400, "Invalid photo path")
    if not target.exists():
        raise HTTPException(404, "photo not found")
    return FileResponse(str(target))


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if token != config.API_TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    jobs.register_client(websocket)
    try:
        scans = [_enrich(s) for s in db.list_scans(limit=50)]
        await websocket.send_json({"type": "hello", "scans": scans})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("WebSocket error: %s", e)
    finally:
        jobs.unregister_client(websocket)
