"""SQLite-backed state for sessions, their logs, and the repo registry.

Single-process, single-thread-at-a-time usage (the scheduler's own
background thread and FastAPI's threadpool both call in here), so a
fresh short-lived connection per call is simple and fast enough for a
local tool. No ORM: the schema is small and the queries are simple.
"""

import sqlite3
import time
from pathlib import Path
from typing import Any

_DB_PATH: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_type TEXT NOT NULL DEFAULT 'code',
    repo TEXT,
    issue_number INTEGER,
    issue_title TEXT NOT NULL DEFAULT '',
    pr_number INTEGER,
    branch_name TEXT,
    harness_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    container_id TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    last_seen_comment_id INTEGER NOT NULL DEFAULT 0,
    last_activity_at REAL NOT NULL,
    pending_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_logs_session ON session_logs(session_id);

CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
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
    session_type: str = "code",
    repo: str | None = None,
    issue_number: int | None = None,
    issue_title: str = "",
    branch_name: str | None = None,
    pending_message: str | None = None,
) -> int:
    now = time.time()
    # Nothing to run yet without an opening message -- sit idle (e.g. a
    # bare session created with nothing filled in) rather than launching
    # a turn with an empty prompt.
    status = "queued" if pending_message else "idle"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions
                (session_type, repo, issue_number, issue_title, branch_name,
                 pending_message, status, last_activity_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_type, repo, issue_number, issue_title, branch_name,
             pending_message, status, now, now, now),
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
    "repo",
    "issue_number",
    "issue_title",
    "branch_name",
    "pr_number",
    "status",
    "container_id",
    "turn_count",
    "last_seen_comment_id",
    "last_activity_at",
    "pending_message",
    "harness_session_id",
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


def create_repo(full_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO repos (full_name, created_at) VALUES (?, ?)",
            (full_name, time.time()),
        )


def list_repos() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM repos ORDER BY full_name ASC").fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_repo(repo_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
