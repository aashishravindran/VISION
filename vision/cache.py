"""Simple key-value cache backed by SQLite (with WAL for concurrent access).

Switched from DuckDB to SQLite because DuckDB enforces a single-process file
lock — fine for analytics workloads, but our cache is hit by every tool call
across the FastAPI worker pool, and yfinance/heatmap loops can serialize
hundreds of writes in a single request. SQLite + WAL handles this correctly.
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from vision.config import ROOT

CACHE_TTL_HOURS_DEFAULT = 12

DB_PATH = ROOT / "cache" / "vision_cache.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_init_lock = threading.Lock()
_initialized = False


def _key(tool: str, params: dict) -> str:
    payload = json.dumps({"tool": tool, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _conn() -> sqlite3.Connection:
    """Get a per-call SQLite connection. WAL allows concurrent readers + one writer."""
    global _initialized
    con = sqlite3.connect(str(DB_PATH), timeout=10.0, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    if not _initialized:
        with _init_lock:
            if not _initialized:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS kv (
                        key TEXT PRIMARY KEY,
                        tool TEXT,
                        params TEXT,
                        value TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                _initialized = True
    return con


def get(tool: str, params: dict, ttl_hours: float = CACHE_TTL_HOURS_DEFAULT) -> Optional[Any]:
    k = _key(tool, params)
    con = _conn()
    try:
        row = con.execute(
            "SELECT value, created_at FROM kv WHERE key = ?", [k]
        ).fetchone()
        if not row:
            return None
        value, created_at_str = row
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except Exception:
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > timedelta(hours=ttl_hours):
            return None
        return json.loads(value)
    finally:
        con.close()


def put(tool: str, params: dict, value: Any) -> None:
    k = _key(tool, params)
    con = _conn()
    try:
        con.execute(
            "INSERT OR REPLACE INTO kv (key, tool, params, value, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                k,
                tool,
                json.dumps(params, sort_keys=True, default=str),
                json.dumps(value, default=str),
                datetime.now(timezone.utc).isoformat(),
            ],
        )
    finally:
        con.close()
