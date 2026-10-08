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

-- Local models Bertha can run sessions on. Discovered from the model server;
-- the tier and description are for choosing between them.
CREATE TABLE IF NOT EXISTS models (
    key TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Code search for agents (app/code_index.py), built with the wiki: the repo
-- map, and source chunks with their embeddings (float32 bytes).
CREATE TABLE IF NOT EXISTS code_maps (
    repo TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    map TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    error TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_code_chunks_repo ON code_chunks(repo);

-- GitHub tools for agents (app/github_tools.py). A grant is one turn's key,
-- scoped to the session's repo; it's revoked when the turn ends.
CREATE TABLE IF NOT EXISTS tool_grants (
    key TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    repo TEXT NOT NULL,
    max_edits INTEGER NOT NULL,
    max_closes INTEGER NOT NULL,
    edits INTEGER NOT NULL DEFAULT 0,
    closes INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

-- Every GitHub tool call, with the issue's values before and after a change,
-- so a run's changes can be reviewed and undone.
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    repo TEXT NOT NULL,
    tool TEXT NOT NULL,
    issue_number INTEGER,
    args_json TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    undone_at REAL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);

-- Prompts that run on a cron schedule, each run as a normal session.
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    repo TEXT,
    prompt TEXT NOT NULL,
    cron TEXT NOT NULL,
    timezone TEXT NOT NULL,
    timeout_minutes INTEGER NOT NULL,
    model TEXT,
    session_type TEXT NOT NULL DEFAULT 'code',
    github_tools INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    session_id INTEGER,
    status TEXT NOT NULL,
    reason TEXT,
    scheduled_for REAL NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL
);

CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id);

-- Settings the user can change in the UI, stored as text. Defaults and limits
-- live in app/agent_settings.py.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
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
        _add_missing_session_columns(conn)


def _add_missing_session_columns(conn: sqlite3.Connection) -> None:
    """Columns added after a database was first created. CREATE TABLE IF
    NOT EXISTS won't add them to an existing table, so do it here.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "attention" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN attention TEXT")
    if "workspace" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN workspace TEXT")
    if "model" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN model TEXT")
    if "model_pinned" not in cols:
        # 1 when the user chose the model; routing leaves a pinned session alone.
        conn.execute("ALTER TABLE sessions ADD COLUMN model_pinned INTEGER NOT NULL DEFAULT 0")
    if "stream_log_id" not in cols:
        # The log entry a running code turn's output streams into.
        conn.execute("ALTER TABLE sessions ADD COLUMN stream_log_id INTEGER")
    if "github_tools" not in cols:
        # 1 when the agent gets the GitHub tools (app/github_tools.py) for its repo.
        conn.execute("ALTER TABLE sessions ADD COLUMN github_tools INTEGER NOT NULL DEFAULT 0")
    if "schedule_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN schedule_id INTEGER")
    if "deadline_at" not in cols:
        # A scheduled run past this time (UTC epoch) is stopped as timed out.
        conn.execute("ALTER TABLE sessions ADD COLUMN deadline_at REAL")
    if "dry_run" not in cols:
        # 1 for eval runs: the turn commits in its workspace but never pushes or opens a PR.
        conn.execute("ALTER TABLE sessions ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0")
    if "last_turn_failed" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_turn_failed INTEGER NOT NULL DEFAULT 0")
    if "spec_phase" not in cols:
        # Spec-first sessions (app/specs.py): drafting, review, building, built or abandoned.
        conn.execute("ALTER TABLE sessions ADD COLUMN spec_phase TEXT")
    if "spec_path" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN spec_path TEXT")
    if "comments_seen_at" not in cols:
        # The newest PR comment or review already acted on (UTC epoch). Comments,
        # line comments and reviews have separate id sequences, so time is the
        # one cursor that covers all three.
        conn.execute("ALTER TABLE sessions ADD COLUMN comments_seen_at REAL")
    if "ci_sha" not in cols:
        # The PR head commit whose CI Bertha last acted on. Null: CI isn't
        # watched (no push yet); "": watch the next head.
        conn.execute("ALTER TABLE sessions ADD COLUMN ci_sha TEXT")
    if "ci_fix_attempts" not in cols:
        # Turns Bertha has started to fix failing CI since it last passed or you spoke.
        conn.execute("ALTER TABLE sessions ADD COLUMN ci_fix_attempts INTEGER NOT NULL DEFAULT 0")
    grant_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_grants)").fetchall()}
    if grant_cols and "scope" not in grant_cols:
        # What the key allows (app/github_tools.py): read, own or full.
        conn.execute("ALTER TABLE tool_grants ADD COLUMN scope TEXT NOT NULL DEFAULT 'full'")
    if grant_cols and "pushes" not in grant_cols:
        conn.execute("ALTER TABLE tool_grants ADD COLUMN pushes INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE tool_grants ADD COLUMN max_pushes INTEGER NOT NULL DEFAULT 0")
    model_cols = {row[1] for row in conn.execute("PRAGMA table_info(models)").fetchall()}
    if model_cols and "context_length" not in model_cols:
        # Tokens the model can take, as the server reports it: the loaded
        # context if it's loaded, else its maximum. Null when unknown.
        conn.execute("ALTER TABLE models ADD COLUMN context_length INTEGER")
    if model_cols and "loaded" not in model_cols:
        # 1 loaded in the server now, 0 not loaded, null unknown.
        conn.execute("ALTER TABLE models ADD COLUMN loaded INTEGER")
    page_cols = {row[1] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()}
    if "commit_sha" not in page_cols:
        conn.execute("ALTER TABLE wiki_pages ADD COLUMN commit_sha TEXT")


def workspace_of(session: dict[str, Any]) -> str:
    """The docker volume holding a session's checkout and OpenCode state.
    Sessions made before per-session names were introduced have none
    stored, and keep the id-based volume they were created with.
    """
    return session.get("workspace") or f"factory-ws-{session['id']}"


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
    model: str | None = None,
    model_pinned: bool = False,
    dry_run: bool = False,
    github_tools: bool = False,
    schedule_id: int | None = None,
    deadline_at: float | None = None,
    spec_first: bool = False,
) -> int:
    now = time.time()
    # Nothing to run yet without an opening message -- sit idle (e.g. a
    # bare session created with nothing filled in) rather than launching
    # a turn with an empty prompt.
    status = "queued" if pending_message else "idle"
    with _connect() as conn:
        # The ref names the branch and the workspace volume, so it has to be
        # unique among sessions. Ids aren't: a fresh database starts again at
        # 1 and would reuse names left behind by earlier sessions.
        ref = uuid.uuid4().hex[:7]
        while conn.execute("SELECT 1 FROM sessions WHERE ref = ?", (ref,)).fetchone():
            ref = uuid.uuid4().hex[:7]
        cur = conn.execute(
            """
            INSERT INTO sessions
                (ref, session_type, repo, issue_number, title, branch_name,
                 pending_message, status, workspace, model, model_pinned, dry_run,
                 github_tools, schedule_id, deadline_at, spec_phase,
                 last_activity_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ref, session_type, repo, issue_number, title, branch_name,
             pending_message, status, f"factory-ws-{ref}", model, 1 if model_pinned else 0,
             1 if dry_run else 0, 1 if github_tools else 0, schedule_id, deadline_at,
             "drafting" if spec_first else None,
             now, now, now),
        )
        return cur.lastrowid


def get_session_by_ref(ref: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE ref = ?", (ref,)).fetchone()
        return _row_to_dict(row) if row else None


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
    "attention",
    "model",
    "model_pinned",
    "last_turn_failed",
    "github_tools",
    "schedule_id",
    "deadline_at",
    "stream_log_id",
    "spec_phase",
    "spec_path",
    "comments_seen_at",
    "ci_sha",
    "ci_fix_attempts",
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
    # Sending a message answers whatever the session was waiting on.
    update_session(session_id, pending_message=text, attention=None)


def clear_pending_message(session_id: int) -> None:
    update_session(session_id, pending_message=None)


def append_log(session_id: int, kind: str, content: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO session_logs (session_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, kind, content, time.time()),
        )
        return cur.lastrowid


def set_log_content(log_id: int, content: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE session_logs SET content = ? WHERE id = ?", (content, log_id))


def extend_log(log_id: int, content: str) -> None:
    """Adds text to the end of an existing log entry, so a turn's output
    grows in one place while it runs.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE session_logs SET content = content || ? WHERE id = ?",
            (content, log_id),
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
            conn.execute("DELETE FROM code_chunks WHERE repo = ?", (row["full_name"],))
            conn.execute("DELETE FROM code_maps WHERE repo = ?", (row["full_name"],))
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


def save_wiki_page(repo: str, page: dict[str, Any], commit_sha: str) -> None:
    """Stores one finished page, as it's written. `commit_sha` records which
    commit it was written from, so a later build can tell it's current.
    """
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_pages
                (repo, slug, title, section, position, body, commit_sha, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, slug) DO UPDATE SET
                title = excluded.title,
                section = excluded.section,
                position = excluded.position,
                body = excluded.body,
                commit_sha = excluded.commit_sha,
                updated_at = excluded.updated_at
            """,
            (repo, page["slug"], page["title"], page["section"], page["position"],
             page["body"], commit_sha, now, now),
        )


def wiki_page_commits(repo: str) -> dict[str, str]:
    """The commit each stored page of this repo was written from."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT slug, commit_sha FROM wiki_pages WHERE repo = ? AND commit_sha IS NOT NULL",
            (repo,),
        ).fetchall()
        return {r["slug"]: r["commit_sha"] for r in rows}


def prune_wiki_pages(repo: str, slugs: list[str]) -> None:
    """Drops this repo's pages that aren't in the finished build's list."""
    with _connect() as conn:
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


# -- models -------------------------------------------------------------

def list_models() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM models ORDER BY key").fetchall()
    models = [_row_to_dict(r) for r in rows]
    for m in models:
        m["enabled"] = bool(m["enabled"])
    return models


def get_model(key: str) -> dict[str, Any] | None:
    return next((m for m in list_models() if m["key"] == key), None)


def add_discovered_model(key: str, model_id: str, tier: str) -> None:
    """Registers a model the server reports. A model already registered keeps
    its tier, description and enabled flag, which the user may have changed.
    """
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO models (key, model_id, tier, description, enabled, created_at, updated_at)
            VALUES (?, ?, ?, '', 1, ?, ?)
            """,
            (key, model_id, tier, now, now),
        )


def set_model_context(key: str, context_length: int | None, loaded: bool | None = None) -> None:
    """Records what the server says about a model's context and whether it's
    loaded. Not user settings, so they're updated whenever the server is asked.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE models SET context_length = ?, loaded = ? WHERE key = ?",
            (context_length, None if loaded is None else int(loaded), key),
        )


def update_model(key: str, **fields: Any) -> None:
    allowed = {"tier", "description", "enabled"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown model fields: {unknown}")
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    assignments = ", ".join(f"{k} = ?" for k in [*fields, "updated_at"])
    with _connect() as conn:
        conn.execute(
            f"UPDATE models SET {assignments} WHERE key = ?",
            [*fields.values(), time.time(), key],
        )


# -- GitHub tool grants and calls -----------------------------------------

def create_grant(session_id: int, repo: str, max_edits: int, max_closes: int,
                 scope: str = "full", max_pushes: int = 0) -> str:
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tool_grants (key, session_id, repo, max_edits, max_closes, scope, max_pushes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, session_id, repo, max_edits, max_closes, scope, max_pushes, time.time()),
        )
    return key


def get_grant(key: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tool_grants WHERE key = ? AND revoked = 0", (key,)).fetchone()
        return _row_to_dict(row) if row else None


def use_grant(key: str, kind: str) -> bool:
    """Counts one write against the grant's cap. False when the cap is reached."""
    column, cap = {"close": ("closes", "max_closes"), "push": ("pushes", "max_pushes")}.get(kind, ("edits", "max_edits"))
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE tool_grants SET {column} = {column} + 1 WHERE key = ? AND revoked = 0 AND {column} < {cap}",
            (key,),
        )
        return cur.rowcount == 1


def revoke_grants(session_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE tool_grants SET revoked = 1 WHERE session_id = ?", (session_id,))


def record_tool_call(session_id: int, repo: str, tool: str, issue_number: int | None, args: dict,
                     before: dict | None, after: dict | None, ok: bool, error: str | None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tool_calls (session_id, repo, tool, issue_number, args_json, before_json, after_json,
                                    ok, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, repo, tool, issue_number, json.dumps(args),
             None if before is None else json.dumps(before), None if after is None else json.dumps(after),
             1 if ok else 0, error, time.time()),
        )
        return cur.lastrowid


def list_tool_calls(session_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    calls = []
    for r in rows:
        call = _row_to_dict(r)
        for field in ("args", "before", "after"):
            raw = call.pop(f"{field}_json")
            call[field] = json.loads(raw) if raw else None
        call["ok"] = bool(call["ok"])
        calls.append(call)
    return calls


def last_github_change(repo: str, issue_number: int) -> float | None:
    """When Bertha last changed this issue (a successful, not undone write)."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(created_at) AS at FROM tool_calls
            WHERE repo = ? AND issue_number = ? AND ok = 1 AND undone_at IS NULL
              AND tool IN ('update_issue', 'comment_issue', 'close_issue')
            """,
            (repo, issue_number),
        ).fetchone()
        return row["at"] if row else None


def mark_tool_call_undone(call_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE tool_calls SET undone_at = ? WHERE id = ?", (time.time(), call_id))


# -- schedules -------------------------------------------------------------

_SCHEDULE_FIELDS = {"name", "repo", "prompt", "cron", "timezone", "timeout_minutes", "model",
                    "session_type", "github_tools", "enabled", "next_run_at"}


def _schedule_row(row: sqlite3.Row) -> dict[str, Any]:
    schedule = _row_to_dict(row)
    schedule["github_tools"] = bool(schedule["github_tools"])
    schedule["enabled"] = bool(schedule["enabled"])
    return schedule


def create_schedule(**fields: Any) -> int:
    now = time.time()
    unknown = set(fields) - _SCHEDULE_FIELDS
    if unknown:
        raise ValueError(f"unknown schedule fields: {unknown}")
    values = {**fields, "created_at": now, "updated_at": now}
    with _connect() as conn:
        cur = conn.execute(
            f"INSERT INTO schedules ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )
        return cur.lastrowid


def update_schedule(schedule_id: int, **fields: Any) -> None:
    unknown = set(fields) - _SCHEDULE_FIELDS
    if unknown:
        raise ValueError(f"unknown schedule fields: {unknown}")
    fields["updated_at"] = time.time()
    with _connect() as conn:
        conn.execute(
            f"UPDATE schedules SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
            [*fields.values(), schedule_id],
        )


def get_schedule(schedule_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return _schedule_row(row) if row else None


def list_schedules() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
        return [_schedule_row(r) for r in rows]


def delete_schedule(schedule_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (schedule_id,))
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def due_schedules(now: float) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?", (now,)
        ).fetchall()
        return [_schedule_row(r) for r in rows]


def create_schedule_run(schedule_id: int, status: str, scheduled_for: float,
                        session_id: int | None = None, reason: str | None = None) -> int:
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO schedule_runs (schedule_id, session_id, status, reason, scheduled_for, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (schedule_id, session_id, status, reason, scheduled_for, now, None if status == "running" else now),
        )
        return cur.lastrowid


def finish_schedule_run(session_id: int, status: str, reason: str | None = None) -> None:
    """Closes the open run for a session, if it has one."""
    with _connect() as conn:
        conn.execute(
            "UPDATE schedule_runs SET status = ?, reason = COALESCE(?, reason), finished_at = ? "
            "WHERE session_id = ? AND status = 'running'",
            (status, reason, time.time(), session_id),
        )


def list_schedule_runs(schedule_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, s.ref AS session_ref, s.status AS session_status
            FROM schedule_runs r LEFT JOIN sessions s ON s.id = r.session_id
            WHERE r.schedule_id = ? ORDER BY r.id DESC LIMIT ?
            """,
            (schedule_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def last_schedule_run(schedule_id: int) -> dict[str, Any] | None:
    runs = list_schedule_runs(schedule_id, limit=1)
    return runs[0] if runs else None


# -- code index -----------------------------------------------------------

def save_code_index(repo: str, index: dict[str, Any]) -> None:
    """Replaces a repo's code map and chunks in one transaction."""
    from .code_index import to_blob

    chunks = index.get("chunks") or []
    with _connect() as conn:
        conn.execute("DELETE FROM code_chunks WHERE repo = ?", (repo,))
        conn.executemany(
            "INSERT INTO code_chunks (repo, path, start_line, end_line, text, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            [(repo, c["path"], c["start"], c["end"], c["text"], to_blob(c["embedding"]))
             for c in chunks if c.get("embedding")],
        )
        conn.execute(
            """
            INSERT INTO code_maps (repo, commit_sha, map, chunk_count, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET commit_sha = excluded.commit_sha, map = excluded.map,
                chunk_count = excluded.chunk_count, error = excluded.error, updated_at = excluded.updated_at
            """,
            (repo, index["commit_sha"], index.get("map") or "", len(chunks), index.get("error"), time.time()),
        )


def get_code_map(repo: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM code_maps WHERE repo = ?", (repo,)).fetchone()
        return _row_to_dict(row) if row else None


def load_code_chunks(repo: str) -> list[dict[str, Any]]:
    """A repo's chunks with their embeddings as float arrays ("vector")."""
    from .code_index import from_blob

    with _connect() as conn:
        rows = conn.execute(
            "SELECT path, start_line, end_line, text, embedding FROM code_chunks WHERE repo = ?", (repo,)
        ).fetchall()
    return [
        {"path": r["path"], "start": r["start_line"], "end": r["end_line"], "text": r["text"],
         "vector": from_blob(r["embedding"])}
        for r in rows
    ]


# -- settings -----------------------------------------------------------

def get_settings_values() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_settings_values(values: dict[str, str]) -> None:
    now = time.time()
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [(k, v, now) for k, v in values.items()],
        )
