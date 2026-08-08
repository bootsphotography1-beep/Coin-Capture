"""Background job queue and live event broadcast.

The scanner posts a photo; we return immediately so the phone can keep scanning.
A thread pool worker picks up the scan, runs identification + condition +
rarity, and updates the row in SQLite. Connected dashboard WebSockets
receive each state change in real time.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any
import weakref

import config
import db
import ollama_client
import rarity
import condition
import comparables

log = logging.getLogger("coinscope.jobs")

# Connected WebSocket clients. Weak refs so closed sockets can be GC'd.
_clients: weakref.WeakSet = weakref.WeakSet()
_loop: asyncio.AbstractEventLoop | None = None

# Bounded thread pool. Ollama is single-streamed per model, so a small pool
# prevents the Mac from queueing 50 concurrent model calls.
_executor = ThreadPoolExecutor(
    max_workers=int(__import__("os").environ.get("COINSCOPE_WORKERS", "1")),
    thread_name_prefix="coinscope",
)


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def register_client(ws) -> None:
    _clients.add(ws)


def unregister_client(ws) -> None:
    _clients.discard(ws)


async def broadcast(event: dict[str, Any]) -> None:
    """Send an event to every connected dashboard WebSocket. Drops on error."""
    if not _clients:
        return
    msg = json.dumps(event, default=str, ensure_ascii=False)
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _emit_sync(event: dict[str, Any]) -> None:
    """Schedule a broadcast from a worker thread."""
    if _loop is None:
        log.warning("No event loop registered; dropping event %s", event.get("type"))
        return
    asyncio.run_coroutine_threadsafe(broadcast(event), _loop)


def enqueue_scan(scan_id: int, group_id: str) -> None:
    """Submit identification + rarity processing for one scan."""
    _executor.submit(_process_scan, scan_id, group_id)


def _process_scan(scan_id: int, group_id: str) -> None:
    """Worker thread: identify + research. Updates DB and broadcasts.

    Idempotent: if a scan is re-enqueued (e.g. the reverse photo was just
    attached to a previously-completed scan), we re-run identification on
    the reverse photo (if not already done), refresh the rarity report,
    and re-broadcast."""
    try:
        scan = db.get_scan(scan_id)
        if not scan:
            log.warning("Scan %s disappeared before processing", scan_id)
            return

        front_path = scan.get("front_photo_path")
        reverse_path = scan.get("reverse_photo_path")
        ident = scan.get("identification")
        already_complete = scan.get("status") == "complete" and ident

        # First pass on the front, or refresh if front was processed but we have
        # no identification cached.
        if not ident and front_path:
            db.set_status(scan_id, "identifying")
            _emit_sync({"type": "scan.status", "scan_id": scan_id, "group_id": group_id, "status": "identifying"})
            try:
                with open(front_path, "rb") as f:
                    front_bytes = f.read()
                ident = ollama_client.identify_coin(front_bytes)
                db.set_identification(scan_id, ident)
                _emit_sync({
                    "type": "scan.identification",
                    "scan_id": scan_id,
                    "group_id": group_id,
                    "identification": ident,
                })
                # Condition estimate from the front photo (best signal).
                cond = condition.estimate_condition(front_bytes)
                db.set_condition(scan_id, cond)
                _emit_sync({
                    "type": "scan.condition",
                    "scan_id": scan_id,
                    "group_id": group_id,
                    "condition": cond,
                })
            except ollama_client.OllamaError as e:
                log.exception("Identification failed for scan %s", scan_id)
                db.set_status(scan_id, "error", error=str(e))
                _emit_sync({"type": "scan.error", "scan_id": scan_id, "group_id": group_id, "error": str(e)})
                return

        # If a reverse photo is present and we haven't described it yet, do that.
        if reverse_path and (not ident or "reverse_identification" not in ident):
            try:
                with open(reverse_path, "rb") as f:
                    rev_bytes = f.read()
                rev_ident = ollama_client.identify_coin(rev_bytes)
                ident["reverse_identification"] = rev_ident
                db.set_identification(scan_id, ident)
                # If the reverse is sharper/better-lit than the front, use it for condition.
                rev_cond = condition.estimate_condition(rev_bytes)
                # Keep whichever condition has higher confidence.
                cur_cond = scan.get("condition") or {}
                if rev_cond.get("confidence", 0) > cur_cond.get("confidence", 0):
                    db.set_condition(scan_id, rev_cond)
            except Exception as e:
                log.warning("Reverse photo identification failed: %s", e)

        # Rarity research — skip if we already did this and nothing changed.
        if not ident:
            db.set_status(scan_id, "error", error="No identification available")
            _emit_sync({"type": "scan.error", "scan_id": scan_id, "group_id": group_id, "error": "No identification"})
            return

        if scan.get("rarity") and not reverse_path:
            # Already researched and no new info — just make sure status is complete.
            db.set_status(scan_id, "complete")
            return

        db.set_status(scan_id, "researching")
        _emit_sync({"type": "scan.status", "scan_id": scan_id, "group_id": group_id, "status": "researching"})

        from canonical import lookup_canonical  # local import to avoid cycle
        report: dict | None = None
        if config.USE_CANONICAL_SHORTCUT:
            report = lookup_canonical(ident)
        if report is None:
            report = rarity.research_rarity(ident)

        db.set_rarity(scan_id, report)
        db.set_status(scan_id, "complete")
        _emit_sync({
            "type": "scan.complete",
            "scan_id": scan_id,
            "group_id": group_id,
            "rarity": report,
        })

        # Duplicate detection: if the same coin (series/year/mint) was scanned
        # recently, flag it on the dashboard. Useful for catching re-scans.
        try:
            dupes = db.find_duplicates_by_fingerprint(
                ident.get("series") or "",
                str(ident.get("year") or ""),
                ident.get("mint_mark"),
            )
            dupes = [d for d in dupes if d["id"] != scan_id][:5]
            if dupes:
                _emit_sync({
                    "type": "scan.duplicates",
                    "scan_id": scan_id,
                    "group_id": group_id,
                    "duplicates": dupes,
                })
        except Exception as e:
            log.warning("Duplicate check failed for scan %s: %s", scan_id, e)

        # Enrich with comparables (Wikipedia summary + auction source links).
        # Best-effort: a network failure here doesn't undo the scan.
        try:
            comp = comparables.find_comparables(ident)
            db.set_comparables(scan_id, comp)
            _emit_sync({
                "type": "scan.comparables",
                "scan_id": scan_id,
                "group_id": group_id,
                "comparables": comp,
            })
        except Exception as e:
            log.warning("Comparables lookup failed for scan %s: %s", scan_id, e)

    except Exception as e:
        log.exception("Unhandled error in scan %s", scan_id)
        db.set_status(scan_id, "error", error=f"Internal: {e}")
        _emit_sync({"type": "scan.error", "scan_id": scan_id, "group_id": group_id, "error": str(e)})


def new_group_id() -> str:
    return uuid.uuid4().hex[:12]