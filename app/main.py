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

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn

from . import agent_settings, cron, github_tools
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
    runner = DockerRunner(settings.agent_image_tag, settings.wiki_image_tag, isolate=settings.egress_isolation)
    runner.ensure_image(AGENT_IMAGE_DIR)
    runner.ensure_image(WIKI_IMAGE_DIR, tag=settings.wiki_image_tag)
    if settings.egress_isolation:
        model_host, model_port = settings.model_host_port
        runner.ensure_egress(BASE_DIR / "app", settings.egress_allow, model_host, model_port,
                             tools_port=settings.tools_port)
    else:
        logger.warning("FACTORY_EGRESS=open: agent containers can reach the host and the internet")
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
    tools_server = _start_tools_server(settings.tools_port)
    logger.info(
        "Agent Factory started, watching %s every %ss (session progress every %ss)",
        settings.repos, settings.poll_interval_seconds, settings.fast_poll_interval_seconds,
    )
    yield
    fast_thread.stop()
    slow_thread.stop()
    tools_server.should_exit = True


def _start_tools_server(port: int) -> uvicorn.Server:
    """The GitHub tool server (app/github_tools.py) on its own port, so agent
    containers can be given a route to it without a route to this API."""
    server = uvicorn.Server(uvicorn.Config(github_tools.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=lambda: asyncio.run(server.serve()), name="factory-tools", daemon=True).start()
    return server


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
    # Eval runs: commit in the workspace, but never push or open a PR.
    dry_run: bool = False
    # Give the agent Bertha's GitHub tools for the session's repo.
    github_tools: bool = False
    # Write a spec for review on a draft PR before building (app/specs.py).
    spec_first: bool = False
    # An issue in the repo this session works on: the PR closes it, a spec is
    # named after it, and its text is the prompt when none is given.
    issue_number: int | None = None


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
    if body.spec_first and (body.session_type != "code" or not body.repo):
        raise HTTPException(400, "a spec-first session needs to be a code session with a repo")
    title = _derive_title(body.prompt)
    if body.issue_number is not None:
        if body.session_type != "code" or not body.repo:
            raise HTTPException(400, "linking an issue needs a code session with a repo")
        gh: GitHubClient = _state["gh"]  # type: ignore[assignment]
        try:
            issue = gh.get_issue(body.repo, body.issue_number)
        except Exception:
            raise HTTPException(400, f"couldn't find issue #{body.issue_number} in {body.repo}")
        title = issue["title"]
        if not (body.prompt or "").strip():
            body.prompt = f"{issue['title']}\n\n{issue['body']}".strip()
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
        issue_number=body.issue_number,
        title=title,
        pending_message=body.prompt,
        model=model_key,
        # A model the user picked stays on this session; the default is routed per turn.
        model_pinned=bool(body.model),
        dry_run=body.dry_run,
        github_tools=body.github_tools and bool(body.repo),
        spec_first=body.spec_first,
    )
    store.append_log(session_id, "system", f"Started a new {body.session_type} session"
                     + (", spec first" if body.spec_first else ""))
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


# -- schedules -------------------------------------------------------------

class ScheduleIn(BaseModel):
    name: str
    prompt: str
    cron: str
    timezone: str
    repo: str | None = None
    timeout_minutes: int = 60
    model: str | None = None
    session_type: str = "code"
    github_tools: bool = False
    enabled: bool = True


class CronPreviewIn(BaseModel):
    cron: str
    timezone: str


def _validate_schedule(body: ScheduleIn) -> float | None:
    """Checks a schedule and returns its next run time (UTC epoch)."""
    if not body.name.strip() or not body.prompt.strip():
        raise HTTPException(400, "a schedule needs a name and a prompt")
    if body.session_type not in ("ask", "code"):
        raise HTTPException(400, "session_type must be 'ask' or 'code'")
    if not 1 <= body.timeout_minutes <= 24 * 60:
        raise HTTPException(400, "the timeout must be between 1 minute and 24 hours")
    if body.repo and body.repo not in {r["full_name"] for r in store.list_repos()}:
        raise HTTPException(400, f"{body.repo} isn't a registered repo")
    if body.github_tools and not body.repo:
        raise HTTPException(400, "GitHub tools need a repo")
    if body.model:
        chosen = store.get_model(body.model)
        if not chosen or not chosen["enabled"]:
            raise HTTPException(400, f"model {body.model} is not an enabled model")
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, f"unknown time zone {body.timezone}")
    try:
        return cron.next_run(body.cron, body.timezone, datetime.now(timezone.utc)).timestamp()
    except cron.CronError as exc:
        raise HTTPException(400, f"cron: {exc}")


def _schedule_view(schedule: dict) -> dict:
    return {**schedule, "last_run": store.last_schedule_run(schedule["id"])}


def _schedule_or_404(schedule_id: int) -> dict:
    schedule = store.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(404, "schedule not found")
    return schedule


@app.get("/api/schedules")
def api_list_schedules():
    return [_schedule_view(s) for s in store.list_schedules()]


@app.post("/api/schedules")
def api_create_schedule(body: ScheduleIn):
    next_run_at = _validate_schedule(body)
    schedule_id = store.create_schedule(**body.model_dump(), next_run_at=next_run_at if body.enabled else None)
    return _schedule_view(store.get_schedule(schedule_id))


@app.post("/api/schedules/preview")
def api_preview_cron(body: CronPreviewIn):
    try:
        ZoneInfo(body.timezone)
        times = cron.upcoming(body.cron, body.timezone, datetime.now(timezone.utc), count=5)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc) or "unknown time zone")
    return {"next": [t.timestamp() for t in times]}


@app.get("/api/schedules/{schedule_id}")
def api_get_schedule(schedule_id: int):
    return _schedule_view(_schedule_or_404(schedule_id))


@app.put("/api/schedules/{schedule_id}")
def api_update_schedule(schedule_id: int, body: ScheduleIn):
    _schedule_or_404(schedule_id)
    next_run_at = _validate_schedule(body)
    store.update_schedule(schedule_id, **body.model_dump(), next_run_at=next_run_at if body.enabled else None)
    return _schedule_view(store.get_schedule(schedule_id))


@app.delete("/api/schedules/{schedule_id}")
def api_delete_schedule(schedule_id: int):
    _schedule_or_404(schedule_id)
    store.delete_schedule(schedule_id)
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/run")
def api_run_schedule_now(schedule_id: int):
    schedule = _schedule_or_404(schedule_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    return scheduler.start_schedule_run(schedule, time.time())


@app.get("/api/schedules/{schedule_id}/runs")
def api_schedule_runs(schedule_id: int):
    _schedule_or_404(schedule_id)
    return store.list_schedule_runs(schedule_id)


# -- GitHub changes made by a session --------------------------------------

@app.get("/api/sessions/{session_id}/github")
def api_session_github_calls(session_id: int):
    _get_or_404(session_id)
    return store.list_tool_calls(session_id)


@app.post("/api/sessions/{session_id}/github/undo")
def api_undo_session_github(session_id: int):
    session = _get_or_404(session_id)
    if session["status"] in ("running", "queued"):
        raise HTTPException(409, "stop the session before undoing its GitHub changes")
    results = github_tools.undo_session(session_id)
    store.append_log(session_id, "system",
                     f"Undid {sum(r['undone'] for r in results)} GitHub change(s) from the UI")
    return results


# -- spec-first sessions ------------------------------------------------------

@app.get("/api/sessions/{session_id}/spec")
def api_session_spec(session_id: int):
    """The session's spec as it is on its branch, with where to review it."""
    session = _get_or_404(session_id)
    if not session.get("spec_phase"):
        raise HTTPException(404, "this session has no spec")
    gh: GitHubClient = _state["gh"]  # type: ignore[assignment]
    path, branch = session.get("spec_path"), session.get("branch_name")
    content = gh.file_at(session["repo"], path, branch) if path and branch and session["pr_number"] else None
    return {
        "phase": session["spec_phase"],
        "path": path,
        "content": content,
        "url": f"https://github.com/{session['repo']}/blob/{branch}/{path}" if content else None,
        "pr_url": f"https://github.com/{session['repo']}/pull/{session['pr_number']}" if session["pr_number"] else None,
    }


def _spec_in_review_or_409(session_id: int) -> dict:
    session = _get_or_404(session_id)
    if session.get("spec_phase") != "review":
        raise HTTPException(409, "the spec isn't waiting on review")
    if session["status"] in ("running", "queued"):
        raise HTTPException(409, "wait for the current turn to finish")
    return session


@app.post("/api/sessions/{session_id}/spec/approve")
def api_approve_spec(session_id: int):
    session = _spec_in_review_or_409(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    scheduler.approve_spec(session, "you, in Bertha", from_ui=True)
    return store.get_session(session_id)


@app.post("/api/sessions/{session_id}/spec/abandon")
def api_abandon_spec(session_id: int):
    session = _spec_in_review_or_409(session_id)
    scheduler: Scheduler = _state["scheduler"]  # type: ignore[assignment]
    scheduler.abandon_spec(session, "you, in Bertha", from_ui=True)
    return store.get_session(session_id)


@app.get("/api/security")
def api_security():
    """How well agent containers are contained, for the Settings screen."""
    settings = get_settings()
    gh: GitHubClient = _state["gh"]  # type: ignore[assignment]
    try:
        token, token_error = gh.token_info(), None
    except Exception as exc:
        token, token_error = None, str(exc) or type(exc).__name__
    repos = []
    for repo in store.list_repos():
        try:
            protected = gh.repo_info(repo["full_name"])["default_branch_protected"]
        except Exception:
            protected = None
        repos.append({"full_name": repo["full_name"], "default_branch_protected": protected})
    limits = agent_settings.current()
    return {
        "egress_isolation": settings.egress_isolation,
        "egress_allow": settings.egress_allow,
        "agent_user": "node (uid 1000), without the GitHub token",
        "container_limits": {
            "memory_gb": limits["container_memory_gb"],
            "cpus": limits["container_cpus"],
            "pids": agent_settings.CONTAINER_PIDS_LIMIT,
        },
        "token": token,
        "token_error": token_error,
        "repos": repos,
    }


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


