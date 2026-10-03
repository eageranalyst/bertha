"""FastAPI app: the REST API. The UI lives in web/ (Next.js) as its
own process, talking to this over HTTP -- see web/lib/api.ts.

The scheduler runs on two background threads (everything it does --
sqlite, docker-py, PyGithub -- is blocking I/O): a fast one for local
session progress and a slow one for GitHub polling, see TickThread and
lifespan() below. API handlers are defined as plain `def`s so FastAPI
dispatches them to its threadpool instead of the asyncio loop, which
keeps this simple: nothing here needs to be async.
"""

import logging
import os
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import store
from .config import get_settings
from .docker_runner import DockerRunner
from .github_client import GitHubClient
from .preflight import run_preflight
from .scheduler import Scheduler

if os.environ.get("FACTORY_SKIP_PREFLIGHT") != "1":
    run_preflight()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("factory.main")

BASE_DIR = Path(__file__).resolve().parent.parent
AGENT_IMAGE_DIR = BASE_DIR / "agent_image"

_state: dict[str, object] = {}


class TickThread(threading.Thread):
    """Runs one tick function on its own interval. There are two of
    these -- see lifespan() -- so local session progress (fast) isn't
    held hostage by the GitHub-polling cadence (slow).
    """

    def __init__(self, name: str, tick_fn: Callable[[], None], interval_seconds: int):
        super().__init__(daemon=True, name=f"factory-{name}")
        self.tick_fn = tick_fn
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_fn()
            except Exception:
                logger.exception("%s tick failed", self.name)
            self._stop.wait(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store.init_db(settings.db_path)
    for repo in settings.repos:
        store.create_repo(repo)
    runner = DockerRunner(settings.agent_image_tag)
    runner.ensure_image(AGENT_IMAGE_DIR)
    gh = GitHubClient(settings.github_token, settings.github_bot_username)
    scheduler = Scheduler(settings, runner, gh)
    fast_thread = TickThread("fast", scheduler.tick_fast, settings.fast_poll_interval_seconds)
    slow_thread = TickThread("slow", scheduler.tick_slow, settings.poll_interval_seconds)
    fast_thread.start()
    slow_thread.start()
    _state["scheduler"] = scheduler
    _state["threads"] = [fast_thread, slow_thread]
    logger.info(
        "Agent Factory started, watching %s every %ss (session progress every %ss)",
        settings.repos, settings.poll_interval_seconds, settings.fast_poll_interval_seconds,
    )
    yield
    fast_thread.stop()
    slow_thread.stop()


app = FastAPI(title="Agent Factory", lifespan=lifespan)

# The UI is a separate Next.js dev/prod server on its own port, not
# served from here -- it needs CORS to call this API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessageIn(BaseModel):
    text: str


class NewSessionIn(BaseModel):
    session_type: str
    repo: str | None = None
    prompt: str | None = None


class RepoIn(BaseModel):
    full_name: str


def _get_or_404(session_id: int) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


@app.get("/api/sessions")
def api_list_sessions():
    return store.list_sessions()


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: int):
    return _get_or_404(session_id)


@app.get("/api/sessions/{session_id}/logs")
def api_get_logs(session_id: int):
    _get_or_404(session_id)
    return store.list_logs(session_id)


@app.post("/api/sessions/{session_id}/message")
def api_send_message(session_id: int, body: MessageIn):
    _get_or_404(session_id)
    store.set_pending_message(session_id, body.text)
    store.append_log(session_id, "user_message", body.text)
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/stop")
def api_stop_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if not scheduler.stop_turn(session):
        raise HTTPException(409, "session is not running")
    return store.get_session(session_id)


def _teardown_volume(session_id: int, scheduler: Scheduler) -> None:
    """Deletes the session's docker volume -- its git clone and
    OpenCode's own conversation state. harness_session_id is cleared
    alongside it so a later Reopen starts a fresh OpenCode session
    instead of trying to -resume one whose backing data is gone.
    """
    scheduler.runner.remove_volume(session_id)
    store.update_session(session_id, harness_session_id=None)


@app.post("/api/sessions/{session_id}/close")
def api_close_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if session["status"] == "running":
        scheduler.stop_turn(session)
    _teardown_volume(session_id, scheduler)
    store.update_session(session_id, status="closed", container_id=None, pending_message=None)
    store.append_log(session_id, "system", "Closed from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/archive")
def api_archive_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if session["status"] == "running":
        scheduler.stop_turn(session)
    _teardown_volume(session_id, scheduler)
    store.update_session(session_id, status="archived", container_id=None, pending_message=None)
    store.append_log(session_id, "system", "Archived from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/reopen")
def api_reopen_session(session_id: int):
    session = _get_or_404(session_id)
    # Only queue a turn if there's actually something to send; otherwise
    # it just goes back to waiting, like a freshly created session.
    status = "queued" if session["pending_message"] else "idle"
    store.update_session(session_id, status=status)
    store.append_log(session_id, "system", "Reopened from UI")
    return store.get_session(session_id)


def _derive_title(prompt: str | None) -> str:
    text = (prompt or "").strip().splitlines()[0] if (prompt or "").strip() else ""
    if len(text) > 72:
        text = text[:71].rstrip() + "…"
    return text or "New session"


@app.post("/api/sessions")
def api_new_session(body: NewSessionIn):
    if body.session_type not in ("ask", "code"):
        raise HTTPException(400, "session_type must be 'ask' or 'code'")
    session_id = store.create_session(
        session_type=body.session_type,
        repo=body.repo,
        title=_derive_title(body.prompt),
        pending_message=body.prompt,
    )
    store.append_log(session_id, "system", f"Started a new {body.session_type} session")
    if body.prompt:
        store.append_log(session_id, "user_message", body.prompt)
    return store.get_session(session_id)


@app.get("/api/repos")
def api_list_repos():
    return store.list_repos()


@app.post("/api/repos")
def api_create_repo(body: RepoIn):
    full_name = body.full_name.strip()
    if "/" not in full_name:
        raise HTTPException(400, "expected owner/repo")
    store.create_repo(full_name)
    return store.list_repos()


@app.delete("/api/repos/{repo_id}")
def api_delete_repo(repo_id: int):
    store.delete_repo(repo_id)
    return store.list_repos()


