"""Background job queue and live event broadcast.

The scanner posts a photo; we return immediately so the phone can keep scanning.
A single GPU worker processes the queue: crop → identify → canonical rarity
(target ~4s on an RTX 4070 Ti). Comparables run after the card is already live.
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

from . import config
from . import crop
from . import db
from . import ollama_client
from . import rarity
from . import rarity_specialist
from . import condition
from . import comparables
from .canonical import lookup_canonical, rarity_flag

log = logging.getLogger("coinscope.jobs")

_clients: weakref.WeakSet = weakref.WeakSet()
_loop: asyncio.AbstractEventLoop | None = None

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
    if _loop is None:
        log.warning("No event loop registered; dropping event %s", event.get("type"))
        return
    asyncio.run_coroutine_threadsafe(broadcast(event), _loop)


def enqueue_scan(scan_id: int, group_id: str) -> None:
    _executor.submit(_process_scan, scan_id, group_id)


def _crop_and_replace(path: str | None) -> bytes | None:
    """Crop the coin in-place so dashboard cards show the coin, not the table."""
    if not path:
        return None
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    orig_path = p.with_name(p.stem + "_original" + p.suffix)
    if orig_path.exists():
        return p.read_bytes()
    original = p.read_bytes()
    try:
        cropped, meta = crop.crop_coin(original)
        # Keep the original next to the cropped file for debugging/reprocess.
        orig_path = p.with_name(p.stem + "_original" + p.suffix)
        if not orig_path.exists():
            orig_path.write_bytes(original)
        p.write_bytes(cropped)
        log.info("Cropped %s via %s", p.name, meta.get("method"))
        return cropped
    except Exception as e:
        log.warning("Crop failed for %s: %s", path, e)
        return original


def _process_scan(scan_id: int, group_id: str) -> None:
    t0 = time.monotonic()
    try:
        scan = db.get_scan(scan_id)
        if not scan:
            log.warning("Scan %s disappeared before processing", scan_id)
            return

        front_path = scan.get("front_photo_path")
        reverse_path = scan.get("reverse_photo_path")
        ident = scan.get("identification")

        front_bytes = _crop_and_replace(front_path) if front_path else None
        rev_bytes = _crop_and_replace(reverse_path) if reverse_path else None

        if not ident and front_bytes:
            db.set_status(scan_id, "identifying")
            _emit_sync({"type": "scan.status", "scan_id": scan_id, "group_id": group_id, "status": "identifying"})
            try:
                ident = ollama_client.identify_coin(front_bytes)
                db.set_identification(scan_id, ident)
                cond = condition.estimate_condition(front_bytes)
                db.set_condition(scan_id, cond)
                _emit_sync({
                    "type": "scan.identification",
                    "scan_id": scan_id,
                    "group_id": group_id,
                    "identification": ident,
                    "condition": cond,
                })
            except ollama_client.OllamaError as e:
                log.exception("Identification failed for scan %s", scan_id)
                db.set_status(scan_id, "error", error=str(e))
                _emit_sync({"type": "scan.error", "scan_id": scan_id, "group_id": group_id, "error": str(e)})
                return

        if reverse_path and rev_bytes and (not ident or "reverse_identification" not in (ident or {})):
            try:
                rev_ident = ollama_client.identify_coin(rev_bytes)
                ident = ident or {}
                ident["reverse_identification"] = rev_ident
                db.set_identification(scan_id, ident)
                rev_cond = condition.estimate_condition(rev_bytes)
                cur_cond = scan.get("condition") or {}
                if rev_cond.get("confidence", 0) > cur_cond.get("confidence", 0):
                    db.set_condition(scan_id, rev_cond)
            except Exception as e:
                log.warning("Reverse photo identification failed: %s", e)

        if not ident:
            db.set_status(scan_id, "error", error="No identification available")
            _emit_sync({"type": "scan.error", "scan_id": scan_id, "group_id": group_id, "error": "No identification"})
            return

        if scan.get("rarity") and not reverse_path:
            db.set_status(scan_id, "complete")
            return

        db.set_status(scan_id, "researching")
        _emit_sync({"type": "scan.status", "scan_id": scan_id, "group_id": group_id, "status": "researching"})

        report: dict | None = None
        rarity_source = None
        if config.USE_CANONICAL_SHORTCUT:
            report = lookup_canonical(ident)
            if report is not None:
                rarity_source = "canonical"

        # Specialist only on a canonical miss — US pocket change should almost
        # always hit the DB so the 4s budget stays intact.
        if report is None and rarity_specialist.has_enough_info_for_specialist(ident):
            try:
                report = rarity_specialist.research_rarity_specialist(ident, timeout_s=20.0)
                rarity_source = "specialist"
            except Exception as e:
                log.warning("Rarity specialist failed for scan %s: %s", scan_id, e)
                report = None

        if report is None:
            report = rarity.research_rarity(ident, timeout_s=20.0)
            rarity_source = report.get("source") or "generic"

        report["rarity_source"] = rarity_source
        report["source"] = rarity_source
        report["flag"] = rarity_flag(report.get("rarity_tier"))
        elapsed = round(time.monotonic() - t0, 2)
        report["elapsed_s"] = elapsed
        db.set_rarity(scan_id, report)
        db.set_status(scan_id, "complete")
        _emit_sync({
            "type": "scan.complete",
            "scan_id": scan_id,
            "group_id": group_id,
            "rarity": report,
            "identification": ident,
            "elapsed_s": elapsed,
        })
        log.info("Scan %s complete in %.2fs flag=%s source=%s", scan_id, elapsed, report.get("flag"), rarity_source)

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

        # After the card is live — Wikipedia etc. must not delay "is it rare?"
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
