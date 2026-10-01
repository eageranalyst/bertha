"""FastAPI app: REST API + the local-only static UI.

The scheduler runs on its own background thread (everything it does --
sqlite, docker-py, PyGithub -- is blocking I/O), ticking on a plain
timer. API handlers are defined as plain `def`s so FastAPI dispatches
them to its threadpool instead of the asyncio loop, which keeps this
simple: nothing here needs to be async.
"""

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .config import get_settings
from .docker_runner import DockerRunner
from .github_client import GitHubClient
from .scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("factory.main")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
AGENT_IMAGE_DIR = BASE_DIR / "agent_image"

_state: dict[str, object] = {}


class SchedulerThread(threading.Thread):
    def __init__(self, scheduler: Scheduler, interval_seconds: int):
        super().__init__(daemon=True, name="factory-scheduler")
        self.scheduler = scheduler
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scheduler.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            self._stop.wait(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store.init_db(settings.db_path)
    runner = DockerRunner(settings.agent_image_tag)
    runner.ensure_image(AGENT_IMAGE_DIR)
    gh = GitHubClient(settings.github_token, settings.github_bot_username)
    scheduler = Scheduler(settings, runner, gh)
    thread = SchedulerThread(scheduler, settings.poll_interval_seconds)
    thread.start()
    _state["scheduler"] = scheduler
    _state["thread"] = thread
    logger.info(
        "Agent Factory started, watching %s every %ss",
        settings.repos, settings.poll_interval_seconds,
    )
    yield
    thread.stop()


app = FastAPI(title="Agent Factory", lifespan=lifespan)


class MessageIn(BaseModel):
    text: str


class NewSessionIn(BaseModel):
    repo: str
    issue_number: int


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


@app.post("/api/sessions/{session_id}/close")
def api_close_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if session["container_id"]:
        scheduler.runner.remove(session["container_id"])
    store.update_session(session_id, status="closed", container_id=None, pending_message=None)
    store.append_log(session_id, "system", "Closed from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/archive")
def api_archive_session(session_id: int):
    _get_or_404(session_id)
    store.update_session(session_id, status="archived")
    store.append_log(session_id, "system", "Archived from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/reopen")
def api_reopen_session(session_id: int):
    _get_or_404(session_id)
    store.update_session(session_id, status="queued")
    store.append_log(session_id, "system", "Reopened from UI")
    return store.get_session(session_id)


@app.post("/api/sessions")
def api_new_session(body: NewSessionIn):
    if store.find_session_by_issue(body.repo, body.issue_number):
        raise HTTPException(409, "session already exists for this issue")
    settings = get_settings()
    gh = GitHubClient(settings.github_token, settings.github_bot_username)
    try:
        issue = gh.get_issue(body.repo, body.issue_number)
    except Exception as exc:
        raise HTTPException(400, f"could not fetch issue: {exc}") from exc
    branch = f"factory/issue-{issue['number']}"
    session_id = store.create_session(
        repo=body.repo,
        issue_number=issue["number"],
        issue_title=issue["title"],
        branch_name=branch,
        claude_session_id=str(uuid.uuid4()),
    )
    store.append_log(session_id, "system", f"Started manually for issue #{issue['number']}")
    store.set_pending_message(session_id, issue["body"] or issue["title"])
    return store.get_session(session_id)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
