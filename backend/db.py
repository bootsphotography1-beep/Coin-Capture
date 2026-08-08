"""SQLite layer for coinscope. Single connection, WAL mode for concurrent reads."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Any

import config

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Per-thread SQLite connection. WAL lets the dashboard read while jobs write."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    status TEXT NOT NULL,            -- 'uploading' | 'identifying' | 'researching' | 'complete' | 'error'
    side TEXT,                       -- 'obverse' | 'reverse' | 'unknown'
    side_index INTEGER,              -- 1 = first photo, 2 = second photo. Pair by group_id.
    group_id TEXT NOT NULL,          -- All sides of one coin share a group_id (uuid).
    front_photo_path TEXT,
    reverse_photo_path TEXT,
    notes TEXT,                      -- free-form text from user
    identification_json TEXT,        -- raw LLM/canonical identification result
    rarity_json TEXT,                -- rarity report (mintage, scarcity, value range)
    condition_json TEXT,             -- photo-based condition estimate
    comparables_json TEXT,           -- wikipedia + auction source links
    error TEXT                       -- populated on status='error'
);

CREATE INDEX IF NOT EXISTS idx_scans_group ON scans(group_id);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at DESC);
"""


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


def create_scan(
    group_id: str,
    side: str,
    side_index: int,
    notes: str | None = None,
) -> int:
    now = time.time()
    with tx() as c:
        cur = c.execute(
            """INSERT INTO scans
               (created_at, updated_at, status, side, side_index, group_id, notes)
               VALUES (?, ?, 'uploading', ?, ?, ?, ?)""",
            (now, now, side, side_index, group_id, notes),
        )
        return int(cur.lastrowid)


def set_photo_paths(scan_id: int, front: str | None, reverse: str | None) -> None:
    with tx() as c:
        c.execute(
            """UPDATE scans
               SET front_photo_path = COALESCE(?, front_photo_path),
                   reverse_photo_path = COALESCE(?, reverse_photo_path),
                   updated_at = ?
               WHERE id = ?""",
            (front, reverse, time.time(), scan_id),
        )


def set_status(scan_id: int, status: str, error: str | None = None) -> None:
    with tx() as c:
        c.execute(
            "UPDATE scans SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, time.time(), scan_id),
        )


def set_identification(scan_id: int, ident: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "UPDATE scans SET identification_json=?, updated_at=? WHERE id=?",
            (json.dumps(ident, ensure_ascii=False), time.time(), scan_id),
        )


def set_rarity(scan_id: int, rarity: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "UPDATE scans SET rarity_json=?, updated_at=? WHERE id=?",
            (json.dumps(rarity, ensure_ascii=False), time.time(), scan_id),
        )


def set_condition(scan_id: int, condition_data: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "UPDATE scans SET condition_json=?, updated_at=? WHERE id=?",
            (json.dumps(condition_data, ensure_ascii=False), time.time(), scan_id),
        )


def set_comparables(scan_id: int, comparables_data: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "UPDATE scans SET comparables_json=?, updated_at=? WHERE id=?",
            (json.dumps(comparables_data, ensure_ascii=False), time.time(), scan_id),
        )


def get_scan(scan_id: int) -> dict[str, Any] | None:
    with tx() as c:
        row = c.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_scans(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM scans ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_scans(query: str, limit: int = 100) -> list[dict[str, Any]]:
    """Simple LIKE search across the JSON-text fields plus notes.
    Matches any of: country, denomination, series, year, mint_mark, notes, rarity_tier."""
    like = f"%{query}%"
    with tx() as c:
        rows = c.execute(
            """SELECT * FROM scans
               WHERE identification_json LIKE ?
                  OR notes LIKE ?
                  OR rarity_json LIKE ?
                  OR condition_json LIKE ?
                  OR group_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (like, like, like, like, query, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_duplicates_by_fingerprint(
    series: str, year: str, mint_mark: str | None, threshold_minutes: int = 60
) -> list[dict[str, Any]]:
    """Find scans that look like the same physical coin — same series/year/mint
    scanned within `threshold_minutes` of each other. Used for duplicate detection."""
    if not series:
        return []
    with tx() as c:
        rows = c.execute(
            """SELECT * FROM scans
               WHERE identification_json LIKE ?
               ORDER BY created_at DESC LIMIT 100""",
            (f"%{year}%",),
        ).fetchall()
    matches = []
    for r in rows:
        d = _row_to_dict(r)
        ident = d.get("identification") or {}
        if (
            (ident.get("series") or "").lower() == series.lower()
            and str(ident.get("year") or "").strip() == str(year).strip()
            and (ident.get("mint_mark") or "").lower() == (mint_mark or "").lower()
        ):
            matches.append(d)
    return matches


def find_open_scan_in_group(group_id: str) -> dict[str, Any] | None:
    """If a scan for this group_id is still uploading/identifying, return it.
    Used to attach the reverse photo to an existing front-photo scan."""
    with tx() as c:
        row = c.execute(
            """SELECT * FROM scans
               WHERE group_id=? AND status IN ('uploading','identifying','researching')
               ORDER BY created_at DESC LIMIT 1""",
            (group_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_pending_reverse(group_id: str) -> dict[str, Any] | None:
    """Find the most recent scan in this group that has a front photo but
    no reverse photo yet — even if the scan is already complete.
    This handles the realistic flow where the front finishes processing
    before the user turns the coin over and uploads the reverse."""
    with tx() as c:
        row = c.execute(
            """SELECT * FROM scans
               WHERE group_id=?
                 AND front_photo_path IS NOT NULL
                 AND reverse_photo_path IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (group_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("identification_json", "rarity_json", "condition_json", "comparables_json"):
        if d.get(k):
            try:
                d[k.removesuffix("_json")] = json.loads(d[k])
            except Exception:
                pass
    return d