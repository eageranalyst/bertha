"""SQLite-backed state for sessions, their logs, and the repo registry.

Single-process, single-thread-at-a-time usage (the scheduler's own
background thread and FastAPI's threadpool both call in here), so a
fresh short-lived connection per call is simple and fast enough for a
local tool. No ORM: the schema is small and the queries are simple.
"""

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("factory.store")

_DB_PATH: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    session_type TEXT NOT NULL DEFAULT 'code',
    repo TEXT,
    issue_number INTEGER,
    title TEXT NOT NULL DEFAULT '',
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

-- One row per repo that has ever been indexed. created_at is when the
-- wiki was first created, last_indexed_at when the most recent build
-- finished, updated_at when the row last changed for any reason.
CREATE TABLE IF NOT EXISTS wiki_indexes (
    repo TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress TEXT,
    commit_sha TEXT,
    scan_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    last_indexed_at REAL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    position INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (repo, slug)
);

-- The global skills library. A skill is one SKILL.md. Updating it in
-- place bumps `version` and keeps its id, metadata and repo links.
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_metadata (
    skill_id INTEGER NOT NULL REFERENCES skills(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (skill_id, key)
);

-- A row here means the skill is enabled for the repo. Disabling deletes it.
CREATE TABLE IF NOT EXISTS repo_skills (
    repo TEXT NOT NULL,
    skill_id INTEGER NOT NULL REFERENCES skills(id),
    created_at REAL NOT NULL,
    PRIMARY KEY (repo, skill_id)
);
"""


def init_db(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        _migrate_pre_redesign_sessions_table(conn)
        conn.executescript(SCHEMA)


def _migrate_pre_redesign_sessions_table(conn: sqlite3.Connection) -> None:
    """One-time, idempotent upgrade of a `sessions` table from before
    the ask/code redesign: no ref/session_type/title columns, and
    repo/issue_number/branch_name were NOT NULL (which the current
    schema needs to relax -- SQLite can't ALTER that away, so this
    recreates the table and copies rows across instead). A no-op once
    a database is current (`ref` column present), and a no-op on a
    brand-new database (the table doesn't exist yet).
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if not cols or "ref" in cols:
        return
    logger.warning("migrating sessions table from pre-redesign schema")
    conn.execute("ALTER TABLE sessions RENAME TO sessions_old")
    conn.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT NOT NULL,
            session_type TEXT NOT NULL DEFAULT 'code',
            repo TEXT,
            issue_number INTEGER,
            title TEXT NOT NULL DEFAULT '',
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
        )
        """
    )
    title_source = "title" if "title" in cols else ("issue_title" if "issue_title" in cols else "''")
    session_type_source = "session_type" if "session_type" in cols else "'code'"
    conn.execute(
        f"""
        INSERT INTO sessions
            (id, ref, session_type, repo, issue_number, title, pr_number,
             branch_name, harness_session_id, status, container_id, turn_count,
             last_seen_comment_id, last_activity_at, pending_message, created_at, updated_at)
        SELECT
            id, substr(lower(hex(randomblob(4))), 1, 7), {session_type_source}, repo, issue_number,
            {title_source}, pr_number, branch_name, harness_session_id, status,
            container_id, turn_count, last_seen_comment_id, last_activity_at,
            pending_message, created_at, updated_at
        FROM sessions_old
        """
    )
    conn.execute("DROP TABLE sessions_old")


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
    title: str = "",
    branch_name: str | None = None,
    pending_message: str | None = None,
) -> int:
    now = time.time()
    ref = uuid.uuid4().hex[:7]
    # Nothing to run yet without an opening message -- sit idle (e.g. a
    # bare session created with nothing filled in) rather than launching
    # a turn with an empty prompt.
    status = "queued" if pending_message else "idle"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions
                (ref, session_type, repo, issue_number, title, branch_name,
                 pending_message, status, last_activity_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ref, session_type, repo, issue_number, title, branch_name,
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
    "title",
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


def get_repo(repo_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_repos() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.full_name, r.created_at,
                   w.status AS wiki_status, w.last_indexed_at AS wiki_last_indexed_at
            FROM repos r
            LEFT JOIN wiki_indexes w ON w.repo = r.full_name
            ORDER BY r.full_name ASC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_repo(repo_id: int) -> None:
    with _connect() as conn:
        row = conn.execute("SELECT full_name FROM repos WHERE id = ?", (repo_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM wiki_pages WHERE repo = ?", (row["full_name"],))
            conn.execute("DELETE FROM wiki_indexes WHERE repo = ?", (row["full_name"],))
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))


# -- wiki -------------------------------------------------------------

_WIKI_INDEX_FIELDS = {"status", "progress", "commit_sha", "scan", "error", "last_indexed_at"}


def get_wiki_index(repo: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM wiki_indexes WHERE repo = ?", (repo,)).fetchone()
    if not row:
        return None
    index = _row_to_dict(row)
    index["scan"] = json.loads(index.pop("scan_json") or "null")
    return index


def upsert_wiki_index(repo: str, **fields: Any) -> None:
    """Creates the wiki_indexes row on first call (which must pass
    status) and updates whichever fields are given afterwards. `scan`
    is stored as JSON.
    """
    unknown = set(fields) - _WIKI_INDEX_FIELDS
    if unknown:
        raise ValueError(f"unknown wiki index fields: {unknown}")
    if "scan" in fields:
        fields["scan_json"] = json.dumps(fields.pop("scan"))
    now = time.time()
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM wiki_indexes WHERE repo = ?", (repo,)).fetchone()
        if exists:
            # created_at is never touched after the first write.
            assignments = ", ".join(f"{k} = ?" for k in [*fields, "updated_at"])
            conn.execute(
                f"UPDATE wiki_indexes SET {assignments} WHERE repo = ?",
                [*fields.values(), now, repo],
            )
            return
        if "status" not in fields:
            raise ValueError("the first write to a wiki index must set status")
        values = {"repo": repo, "created_at": now, "updated_at": now, **fields}
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO wiki_indexes ({columns}) VALUES ({placeholders})",
            list(values.values()),
        )


def fail_interrupted_wiki_indexes() -> None:
    """A build runs on a thread in this process, so one left 'indexing'
    when the process went down will never finish. Mark it failed so the
    UI doesn't show it spinning forever.
    """
    with _connect() as conn:
        conn.execute(
            """
            UPDATE wiki_indexes
            SET status = 'failed', progress = NULL, error = 'Interrupted by a restart', updated_at = ?
            WHERE status = 'indexing'
            """,
            (time.time(),),
        )


def list_wiki_pages(repo: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, title, section, position, created_at, updated_at
            FROM wiki_pages WHERE repo = ? ORDER BY position, id
            """,
            (repo,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_wiki_page(repo: str, slug: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wiki_pages WHERE repo = ? AND slug = ?", (repo, slug)
        ).fetchone()
        return _row_to_dict(row) if row else None


def replace_wiki_pages(repo: str, pages: list[dict[str, Any]]) -> None:
    """Upserts the given pages by slug and drops any page of this repo
    whose slug isn't in the list. One transaction, so a reader never
    sees a half-written wiki.
    """
    now = time.time()
    with _connect() as conn:
        for p in pages:
            conn.execute(
                """
                INSERT INTO wiki_pages (repo, slug, title, section, position, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, slug) DO UPDATE SET
                    title = excluded.title,
                    section = excluded.section,
                    position = excluded.position,
                    body = excluded.body,
                    updated_at = excluded.updated_at
                """,
                (repo, p["slug"], p["title"], p["section"], p["position"], p["body"], now, now),
            )
        slugs = [p["slug"] for p in pages]
        if slugs:
            placeholders = ", ".join("?" for _ in slugs)
            conn.execute(
                f"DELETE FROM wiki_pages WHERE repo = ? AND slug NOT IN ({placeholders})",
                [repo, *slugs],
            )
        else:
            conn.execute("DELETE FROM wiki_pages WHERE repo = ?", (repo,))


# -- skills ------------------------------------------------------------

def _attach_metadata(conn: sqlite3.Connection, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, str]] = {}
    for row in conn.execute("SELECT skill_id, key, value FROM skill_metadata ORDER BY key"):
        by_id.setdefault(row["skill_id"], {})[row["key"]] = row["value"]
    for skill in skills:
        skill["metadata"] = by_id.get(skill["id"], {})
    return skills


def list_skills() -> list[dict[str, Any]]:
    """The library, without skill bodies."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, description, version, created_at, updated_at FROM skills ORDER BY name"
        ).fetchall()
        return _attach_metadata(conn, [_row_to_dict(r) for r in rows])


def get_skill(skill_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not row:
            return None
        return _attach_metadata(conn, [_row_to_dict(row)])[0]


def get_skill_by_name(name: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return _attach_metadata(conn, [_row_to_dict(row)])[0]


def save_skill(name: str, description: str, content: str) -> tuple[dict[str, Any], bool]:
    """Adds a skill, or updates the one with this name. An update that
    changes the content or description bumps the version; the id,
    metadata and repo links stay as they are. Returns (skill, created).
    """
    now = time.time()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO skills (name, description, content, version, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (name, description, content, now, now),
            )
            skill_id, created = cur.lastrowid, True
        else:
            skill_id, created = row["id"], False
            if row["content"] != content or row["description"] != description:
                conn.execute(
                    """
                    UPDATE skills SET description = ?, content = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (description, content, now, skill_id),
                )
    return get_skill(skill_id), created


def set_skill_metadata(skill_id: int, metadata: dict[str, str]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM skill_metadata WHERE skill_id = ?", (skill_id,))
        conn.executemany(
            "INSERT INTO skill_metadata (skill_id, key, value) VALUES (?, ?, ?)",
            [(skill_id, k, v) for k, v in metadata.items()],
        )


def delete_skill(skill_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM repo_skills WHERE skill_id = ?", (skill_id,))
        conn.execute("DELETE FROM skill_metadata WHERE skill_id = ?", (skill_id,))
        conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))


def list_repo_skills(repo: str) -> list[dict[str, Any]]:
    """Every skill enabled for the repo, bodies included. Code sessions
    use this to write the skills into their container.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.name, s.content FROM skills s
            JOIN repo_skills rs ON rs.skill_id = s.id
            WHERE rs.repo = ? ORDER BY s.name
            """,
            (repo,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def library_for_repo(repo: str) -> list[dict[str, Any]]:
    """The whole library, each skill flagged with whether this repo has it."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.description, s.version, s.created_at, s.updated_at,
                   (rs.skill_id IS NOT NULL) AS enabled
            FROM skills s
            LEFT JOIN repo_skills rs ON rs.skill_id = s.id AND rs.repo = ?
            ORDER BY s.name
            """,
            (repo,),
        ).fetchall()
        skills = [_row_to_dict(r) for r in rows]
        for skill in skills:
            skill["enabled"] = bool(skill["enabled"])
        return _attach_metadata(conn, skills)


def set_repo_skill(repo: str, skill_id: int, enabled: bool) -> None:
    with _connect() as conn:
        if enabled:
            conn.execute(
                "INSERT OR IGNORE INTO repo_skills (repo, skill_id, created_at) VALUES (?, ?, ?)",
                (repo, skill_id, time.time()),
            )
        else:
            conn.execute(
                "DELETE FROM repo_skills WHERE repo = ? AND skill_id = ?", (repo, skill_id)
            )
