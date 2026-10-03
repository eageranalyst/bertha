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

from typing import Any

from . import agent_settings
from . import models as model_registry
from . import store
from .config import get_settings
from .skills import parse_skill
from .docker_runner import DockerRunner
from .github_client import GitHubClient
from .preflight import run_preflight
from .scheduler import Scheduler
from .wiki_indexer import WikiIndexer

if os.environ.get("FACTORY_SKIP_PREFLIGHT") != "1":
    run_preflight()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("factory.main")

BASE_DIR = Path(__file__).resolve().parent.parent
AGENT_IMAGE_DIR = BASE_DIR / "agent_image"
WIKI_IMAGE_DIR = BASE_DIR / "wiki_image"

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
    try:
        model_registry.refresh(settings)
    except Exception as exc:
        # The model server may not be up yet; the registry keeps what it has.
        logger.warning("could not discover models from the model server: %s", exc)
    store.fail_interrupted_wiki_indexes()
    for repo in settings.repos:
        store.create_repo(repo)
    runner = DockerRunner(settings.agent_image_tag, settings.wiki_image_tag)
    runner.ensure_image(AGENT_IMAGE_DIR)
    runner.ensure_image(WIKI_IMAGE_DIR, tag=settings.wiki_image_tag)
    gh = GitHubClient(settings.github_token, settings.github_bot_username)
    scheduler = Scheduler(settings, runner, gh)
    fast_thread = TickThread("fast", scheduler.tick_fast, settings.fast_poll_interval_seconds)
    slow_thread = TickThread("slow", scheduler.tick_slow, settings.poll_interval_seconds)
    fast_thread.start()
    slow_thread.start()
    _state["scheduler"] = scheduler
    _state["gh"] = gh
    wiki = WikiIndexer(settings, runner)
    # Build containers outlive this process; pick up their output again.
    wiki.resume()
    _state["wiki"] = wiki
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
    # 3001 too: when another web dev server already holds 3000, Next
    # moves the UI to 3001 and the API would otherwise reject its preflights.
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessageIn(BaseModel):
    text: str


class NewSessionIn(BaseModel):
    session_type: str
    repo: str | None = None
    prompt: str | None = None
    # A registered model key. Omitted, the session gets the default.
    model: str | None = None


class RepoIn(BaseModel):
    full_name: str


class WikiIndexIn(BaseModel):
    force: bool = False


def _repo_or_404(repo_id: int) -> dict:
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")
    return repo


def _get_or_404(session_id: int) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


@app.get("/api/sessions")
def api_list_sessions():
    return store.list_sessions()


@app.get("/api/sessions/by-ref/{ref}")
def api_get_session_by_ref(ref: str):
    session = store.get_session_by_ref(ref)
    if not session:
        raise HTTPException(404, "session not found")
    return session


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


class SessionModelIn(BaseModel):
    # A registered model key pins the session to it. "auto" or null hands the
    # session back to per-turn routing.
    model: str | None = None


@app.put("/api/sessions/{session_id}/model")
def api_set_session_model(session_id: int, body: SessionModelIn):
    _get_or_404(session_id)
    if body.model in (None, "", "auto"):
        store.update_session(session_id, model_pinned=0)
        store.append_log(session_id, "system", "Model set to automatic routing")
    else:
        chosen = store.get_model(body.model)
        if not chosen or not chosen["enabled"]:
            raise HTTPException(400, f"model {body.model} is not an enabled model")
        store.update_session(session_id, model=chosen["key"], model_pinned=1)
        store.append_log(session_id, "system", f"Model pinned to {chosen['key']}")
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
    session = store.get_session(session_id)
    if session:
        scheduler.runner.remove_volume(store.workspace_of(session))
    store.update_session(session_id, harness_session_id=None)


@app.post("/api/sessions/{session_id}/close")
def api_close_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if session["status"] == "running":
        scheduler.stop_turn(session)
    _teardown_volume(session_id, scheduler)
    store.update_session(
        session_id, status="closed", container_id=None, pending_message=None, attention=None
    )
    store.append_log(session_id, "system", "Closed from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/archive")
def api_archive_session(session_id: int):
    session = _get_or_404(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    if session["status"] == "running":
        scheduler.stop_turn(session)
    _teardown_volume(session_id, scheduler)
    store.update_session(
        session_id, status="archived", container_id=None, pending_message=None, attention=None
    )
    store.append_log(session_id, "system", "Archived from UI")
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/reopen")
def api_reopen_session(session_id: int):
    session = _get_or_404(session_id)
    # Only queue a turn if there's actually something to send; otherwise
    # it just goes back to waiting, like a freshly created session.
    status = "queued" if session["pending_message"] else "idle"
    store.update_session(session_id, status=status, attention=None)
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
    settings = get_settings()
    if body.model:
        chosen = store.get_model(body.model)
        if not chosen or not chosen["enabled"]:
            raise HTTPException(400, f"model {body.model} is not an enabled model")
        model_key = chosen["key"]
    else:
        default = model_registry.default_model(settings)
        model_key = default["key"] if default else None
    session_id = store.create_session(
        session_type=body.session_type,
        repo=body.repo,
        title=_derive_title(body.prompt),
        pending_message=body.prompt,
        model=model_key,
        # A model the user picked stays on this session; the default is routed per turn.
        model_pinned=bool(body.model),
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


@app.get("/api/repos/{repo_id}/metadata")
def api_repo_metadata(repo_id: int):
    repo = _repo_or_404(repo_id)
    gh: GitHubClient = _state["gh"]  # type: ignore[assignment]
    github, github_error = None, None
    try:
        github = gh.repo_info(repo["full_name"])
    except Exception as exc:
        github_error = str(exc) or type(exc).__name__
    sessions = [s for s in store.list_sessions() if s["repo"] == repo["full_name"]]
    return {
        "repo": repo,
        "github": github,
        "github_error": github_error,
        "sessions": {
            "total": len(sessions),
            "active": sum(1 for s in sessions if s["status"] not in ("closed", "archived")),
            "with_pr": sum(1 for s in sessions if s["pr_number"] is not None),
        },
        "wiki": store.get_wiki_index(repo["full_name"]),
    }


@app.get("/api/repos/{repo_id}/wiki")
def api_get_wiki(repo_id: int):
    repo = _repo_or_404(repo_id)
    return {
        "index": store.get_wiki_index(repo["full_name"]),
        "pages": store.list_wiki_pages(repo["full_name"]),
    }


@app.get("/api/repos/{repo_id}/wiki/pages/{slug}")
def api_get_wiki_page(repo_id: int, slug: str):
    repo = _repo_or_404(repo_id)
    page = store.get_wiki_page(repo["full_name"], slug)
    if not page:
        raise HTTPException(404, "wiki page not found")
    return page


@app.post("/api/repos/{repo_id}/wiki/index")
def api_index_wiki(repo_id: int, body: WikiIndexIn | None = None):
    repo = _repo_or_404(repo_id)
    wiki: WikiIndexer = _state["wiki"]  # type: ignore[assignment]
    if not wiki.start(repo["full_name"], force=bool(body and body.force)):
        raise HTTPException(409, "this repo's wiki is already being indexed")
    return api_get_wiki(repo_id)


# -- skills library -----------------------------------------------------

class SkillIn(BaseModel):
    content: str


class SkillMetadataIn(BaseModel):
    metadata: dict[str, str]


class RepoSkillIn(BaseModel):
    enabled: bool


def _skill_or_404(skill_id: int) -> dict:
    skill = store.get_skill(skill_id)
    if not skill:
        raise HTTPException(404, "skill not found")
    return skill


@app.get("/api/skills")
def api_list_skills():
    return store.list_skills()


@app.post("/api/skills")
def api_save_skill(body: SkillIn):
    try:
        name, description = parse_skill(body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    skill, created = store.save_skill(name, description, body.content)
    return {**skill, "created": created}


@app.get("/api/skills/by-name/{name}")
def api_get_skill_by_name(name: str):
    skill = store.get_skill_by_name(name)
    if not skill:
        raise HTTPException(404, "skill not found")
    return skill


@app.get("/api/skills/{skill_id}")
def api_get_skill(skill_id: int):
    return _skill_or_404(skill_id)


@app.put("/api/skills/{skill_id}/metadata")
def api_set_skill_metadata(skill_id: int, body: SkillMetadataIn):
    _skill_or_404(skill_id)
    for key, value in body.metadata.items():
        if not key.strip() or len(key) > 64 or len(value) > 500:
            raise HTTPException(400, "metadata keys must be 1-64 characters and values 500 at most")
    store.set_skill_metadata(skill_id, {k.strip(): v for k, v in body.metadata.items()})
    return _skill_or_404(skill_id)


@app.delete("/api/skills/{skill_id}")
def api_delete_skill(skill_id: int):
    _skill_or_404(skill_id)
    store.delete_skill(skill_id)
    return {"ok": True}


@app.get("/api/repos/{repo_id}/skills")
def api_repo_skills(repo_id: int):
    repo = _repo_or_404(repo_id)
    return store.library_for_repo(repo["full_name"])


@app.put("/api/repos/{repo_id}/skills/{skill_id}")
def api_set_repo_skill(repo_id: int, skill_id: int, body: RepoSkillIn):
    repo = _repo_or_404(repo_id)
    _skill_or_404(skill_id)
    store.set_repo_skill(repo["full_name"], skill_id, body.enabled)
    return store.library_for_repo(repo["full_name"])


# -- models -------------------------------------------------------------

class ModelIn(BaseModel):
    tier: str | None = None
    description: str | None = None
    enabled: bool | None = None


@app.get("/api/settings")
def api_get_settings():
    return agent_settings.describe()


@app.put("/api/settings")
def api_update_settings(body: dict[str, Any]):
    try:
        store.set_settings_values(agent_settings.validate_update(body))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return agent_settings.describe()


@app.get("/api/models")
def api_list_models():
    return store.list_models()


@app.post("/api/models/refresh")
def api_refresh_models():
    try:
        return model_registry.refresh(get_settings())
    except Exception as exc:
        raise HTTPException(502, f"could not reach the model server: {exc}")


@app.patch("/api/models/{key}")
def api_update_model(key: str, body: ModelIn):
    if not store.get_model(key):
        raise HTTPException(404, "model not found")
    if body.tier is not None and body.tier not in model_registry.TIERS:
        raise HTTPException(400, f"tier must be one of {', '.join(model_registry.TIERS)}")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    store.update_model(key, **fields)
    return store.get_model(key)


