"""SQLite-backed session and webhook storage.

Two stores:
- sessions: chat session_id → conversation history (list of messages)
- webhooks_in: inbound webhook tokens → metadata + run history
- webhooks_out: outbound subscription rules (alerts) — channels deferred
"""
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vision.config import ROOT

DB_PATH = ROOT / "cache" / "vision_app.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            history_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS webhooks_in (
            id TEXT PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            name TEXT,
            template TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS webhooks_in_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id TEXT NOT NULL,
            payload_json TEXT,
            output TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS webhooks_out (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            trigger_query TEXT NOT NULL,
            schedule_cron TEXT,
            target_url TEXT,
            channel TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_fired_at TEXT
        );
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Sessions ---

def create_session(session_id: str, title: str | None = None) -> dict:
    init_db()
    with _conn() as con:
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            [session_id, title, json.dumps([]), _now(), _now()],
        )
    return get_session(session_id)


def get_session(session_id: str) -> dict | None:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT * FROM sessions WHERE id = ?", [session_id]).fetchone()
    if not row:
        return None
    d = dict(row)
    d["history"] = json.loads(d.pop("history_json"))
    return d


def upsert_session_history(session_id: str, history: list[dict], title: str | None = None) -> None:
    init_db()
    with _conn() as con:
        existing = con.execute("SELECT id FROM sessions WHERE id = ?", [session_id]).fetchone()
        if existing:
            con.execute(
                "UPDATE sessions SET history_json=?, updated_at=?, title=COALESCE(?, title) WHERE id=?",
                [json.dumps(history), _now(), title, session_id],
            )
        else:
            con.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                [session_id, title, json.dumps(history), _now(), _now()],
            )


def list_sessions(limit: int = 50) -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: str) -> None:
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE id = ?", [session_id])


# --- Webhooks (inbound) ---

def create_inbound_webhook(name: str, template: str | None = None) -> dict:
    init_db()
    wid = "wh_in_" + secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    with _conn() as con:
        con.execute(
            "INSERT INTO webhooks_in VALUES (?, ?, ?, ?, ?)",
            [wid, token, name, template, _now()],
        )
    return {"id": wid, "token": token, "name": name, "template": template}


def get_inbound_webhook(token: str) -> dict | None:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT * FROM webhooks_in WHERE token = ?", [token]).fetchone()
    return dict(row) if row else None


def list_inbound_webhooks() -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute("SELECT id, name, template, created_at FROM webhooks_in ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_inbound_webhook(webhook_id: str) -> None:
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM webhooks_in WHERE id = ?", [webhook_id])
        con.execute("DELETE FROM webhooks_in_runs WHERE webhook_id = ?", [webhook_id])


def record_inbound_run(webhook_id: str, payload: dict, output: str) -> None:
    init_db()
    with _conn() as con:
        con.execute(
            "INSERT INTO webhooks_in_runs (webhook_id, payload_json, output, created_at) VALUES (?, ?, ?, ?)",
            [webhook_id, json.dumps(payload, default=str), output, _now()],
        )


# --- Webhooks (outbound — alerts) ---

def create_outbound_alert(
    name: str,
    trigger_query: str,
    schedule_cron: str | None = None,
    target_url: str | None = None,
    channel: str | None = None,
) -> dict:
    init_db()
    aid = "wh_out_" + secrets.token_hex(8)
    with _conn() as con:
        con.execute(
            "INSERT INTO webhooks_out VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)",
            [aid, name, trigger_query, schedule_cron, target_url, channel, _now()],
        )
    return {
        "id": aid, "name": name, "trigger_query": trigger_query,
        "schedule_cron": schedule_cron, "target_url": target_url, "channel": channel,
        "active": True,
    }


def list_outbound_alerts() -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute("SELECT * FROM webhooks_out ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_outbound_alert(alert_id: str) -> None:
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM webhooks_out WHERE id = ?", [alert_id])
