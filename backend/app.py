"""FastAPI app. Serves the scanner page, dashboard page, photo upload,
scan listing, and WebSocket live updates.
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from . import db
from . import jobs
from . import ollama_client
from .canonical import export_canonical

logging.basicConfig(
    level=os.environ.get("COINSCOPE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("coinscope")

app = FastAPI(title="Coinscope", version="0.1.0")


# --- Static assets: scanner + dashboard HTML/CSS/JS live in /static ---
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    jobs.set_event_loop(asyncio.get_running_loop())
    log.info(
        "Coinscope started. Ollama: %s, model: %s, photos: %s",
        config.OLLAMA_BASE,
        config.OLLAMA_MODEL,
        config.PHOTOS_DIR,
    )


# --- Auth ---
def _check_token(request: Request) -> None:
    """All API requests require the shared token in X-Coinscope-Token header."""
    token = request.headers.get("x-coinscope-token") or request.query_params.get("token")
    if token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


# --- Pages ---
@app.get("/", response_class=HTMLResponse)
async def root() -> FileResponse:
    """Default landing: redirect browser users to the dashboard.
    Phone users will navigate to /scan directly."""
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/scan", response_class=HTMLResponse)
async def scanner_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "scanner.html"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


# --- API: health & meta ---
@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Cheap probe. Used by the scanner page on load to confirm it can reach the Mac."""
    return {
        "ok": True,
        "ollama": ollama_client.is_ollama_alive(),
        "model": config.OLLAMA_MODEL,
        "canonical_enabled": config.USE_CANONICAL_SHORTCUT,
    }


@app.get("/api/canonical")
async def canonical_list() -> dict[str, Any]:
    """Expose the canonical coin list so the dashboard can show what's covered."""
    return export_canonical()


# --- API: photo upload ---
@app.post("/api/upload")
async def upload_photo(
    request: Request,
    side: str = Form("unknown"),                  # 'obverse' | 'reverse' | 'unknown'
    side_index: int = Form(1),                    # 1 or 2 — used to pair obverse/reverse
    group_id: str | None = Form(None),            # provided if attaching reverse to existing scan
    notes: str | None = Form(None),
    image: UploadFile = File(...),
) -> JSONResponse:
    _check_token(request)

    # First time through, mint a group id for this physical coin.
    if not group_id:
        group_id = jobs.new_group_id()

    # Enforce: only one front-photo upload per group_id within 3 seconds.
    # This catches the auto-capture firing twice for the same coin
    # (network race, double-tap on the manual button, brief stability flicker).
    if side == "obverse" and db.has_recent_front_upload(group_id, within_seconds=3):
        raise HTTPException(
            status_code=409,
            detail="Front photo already uploaded for this coin — wait 3s before next capture",
        )

    # Read + validate the image. Reject anything we can't decode.
    image_bytes = await image.read()
    if len(image_bytes) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image too large (max {config.MAX_UPLOAD_MB} MB)")
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception as e:
        raise HTTPException(400, f"Invalid image: {e}")

    # Save under photos/<group_id>/<side>_<filename>
    safe_name = (image.filename or "photo.jpg").replace("/", "_").replace("\\", "_")
    scan_dir = config.PHOTOS_DIR / group_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    photo_path = scan_dir / f"{side_index}_{side}_{safe_name}"
    photo_path.write_bytes(image_bytes)

    # If this is a reverse side (side_index=2), try to attach it to an existing
    # scan in this group that has a front photo but no reverse yet.
    # This works whether the front scan is still processing or already complete.
    if side_index == 2:
        pending = db.find_pending_reverse(group_id)
        if pending:
            db.set_photo_paths(pending["id"], front=None, reverse=str(photo_path))
            # Re-enqueue: the worker will pick up the reverse photo and
            # update the identification/rarity with both sides.
            jobs.enqueue_scan(pending["id"], group_id)
            return JSONResponse({
                "ok": True,
                "scan_id": pending["id"],
                "group_id": group_id,
                "attached_to": "existing",
                "photo_path": str(photo_path),
            })

    # Otherwise create a new scan.
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
        "photo_path": str(photo_path),
    })


# --- API: scan list ---
@app.get("/api/scans")
async def api_scans(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    _check_token(request)
    rows = db.list_scans(limit=limit, offset=offset)
    return {"scans": rows}


@app.get("/api/scans/search")
async def api_search_scans(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(100, le=500),
) -> dict[str, Any]:
    """Search across identification fields, rarity report, notes, and group_id.
    V2: powers the dashboard search bar."""
    _check_token(request)
    rows = db.search_scans(q, limit=limit)
    return {"scans": rows, "query": q}


@app.get("/api/scans/export.csv")
async def api_export_csv(request: Request) -> Response:
    """Export all scans as CSV. Columns: id, created, status, country, series,
    year, mint_mark, denomination, rarity_tier, est_value_low, est_value_high,
    condition_band, condition_score, group_id, notes, front_photo, reverse_photo."""
    _check_token(request)
    import csv
    import io
    scans = db.list_scans(limit=10000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "created_at", "status", "country", "denomination", "series",
        "year", "mint_mark", "rarity_tier", "estimated_value_low_usd",
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
    """Export as a plain-text report — useful for printing or pasting elsewhere."""
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
        lines.append(f"     Rarity: {rar.get('rarity_tier', '?')}  ·  Mintage: {rar.get('mintage', '?')}")
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
    return scan


# --- API: photo file (so the dashboard can show the coin image) ---
@app.get("/api/photo")
async def api_photo(request: Request, path: str) -> FileResponse:
    """Stream a stored photo back. Validates the path is under PHOTOS_DIR
    so this can't be used to read arbitrary files."""
    _check_token(request)
    target = Path(path).resolve()
    if not str(target).startswith(str(config.PHOTOS_DIR.resolve())):
        raise HTTPException(400, "Invalid photo path")
    if not target.exists():
        raise HTTPException(404, "photo not found")
    return FileResponse(str(target))


# --- WebSocket: live dashboard updates ---
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Dashboard connects here to receive scan events as they happen."""
    # Token can be sent as query param because browsers can't set headers on WS.
    token = websocket.query_params.get("token")
    if token != config.API_TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    jobs.register_client(websocket)
    try:
        # Send a hello with current state so reconnecting clients get the full picture.
        await websocket.send_json({"type": "hello", "scans": db.list_scans(limit=50)})
        while True:
            # We don't expect incoming messages; receive keeps the connection alive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("WebSocket error: %s", e)
    finally:
        jobs.unregister_client(websocket)