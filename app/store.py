"""SQLite-backed state for sessions and their logs.

Single-process, single-thread-at-a-time usage (one asyncio event loop
driving both the FastAPI handlers and the scheduler tick), so a fresh
short-lived connection per call is simple and fast enough for a local
tool. No ORM: the schema is small and the queries are simple.
"""

import sqlite3
import time
from pathlib import Path
from typing import Any

_DB_PATH: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_title TEXT NOT NULL DEFAULT '',
    pr_number INTEGER,
    branch_name TEXT NOT NULL,
    claude_session_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    container_id TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    last_seen_comment_id INTEGER NOT NULL DEFAULT 0,
    last_activity_at REAL NOT NULL,
    pending_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(repo, issue_number)
);

CREATE TABLE IF NOT EXISTS session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_logs_session ON session_logs(session_id);
"""


def init_db(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("store.init_db() must be called before use")
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_session(
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    branch_name: str,
    claude_session_id: str,
) -> int:
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions
                (repo, issue_number, issue_title, branch_name, claude_session_id,
                 status, last_activity_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (repo, issue_number, issue_title, branch_name, claude_session_id, now, now, now),
        )
        return cur.lastrowid


def find_session_by_issue(repo: str, issue_number: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE repo = ? AND issue_number = ?",
            (repo, issue_number),
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_session(session_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_sessions(status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY updated_at DESC", (status,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


_ALLOWED_FIELDS = {
    "pr_number",
    "status",
    "container_id",
    "turn_count",
    "last_seen_comment_id",
    "last_activity_at",
    "pending_message",
}


def update_session(session_id: int, **fields: Any) -> None:
    unknown = set(fields) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unknown session fields: {unknown}")
    if not fields:
        return
    fields["updated_at"] = time.time()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), session_id]
    with _connect() as conn:
        conn.execute(f"UPDATE sessions SET {columns} WHERE id = ?", values)


def set_pending_message(session_id: int, text: str) -> None:
    update_session(session_id, pending_message=text)


def clear_pending_message(session_id: int) -> None:
    update_session(session_id, pending_message=None)


def append_log(session_id: int, kind: str, content: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO session_logs (session_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, kind, content, time.time()),
        )


def list_logs(session_id: int, limit: int = 500) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM session_logs WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]
