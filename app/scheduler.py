"""The state machine driving every session.

One background thread, one tick every `poll_interval_seconds`. Each
tick is a handful of cheap passes over the sqlite table plus a few
GitHub API calls -- no webhooks, no long-lived connections, nothing
that needs to be reachable from outside this machine.

Two session types:
    ask  -- no repo, no container. The prompt (plus this session's own
            prior turns, reconstructed from its log) goes straight to
            the local model over HTTP from a background thread the
            scheduler owns; the thread writes the result back to the
            row when it's done. Nothing to check on a tick -- there's
            no container to poll.
    code -- the full flow: a container, a persistent per-session
            volume, and (if a repo is attached) a clone/checkout/push/
            PR dance. A code session can exist with no repo attached
            yet -- it just won't be picked up by _start_queued until
            one is, since there's nothing to run against.

Session lifecycle:
    queued    -> a turn is about to start (fresh issue, a UI message,
                 a new PR comment, or just created)
    running   -> in flight: a container (code) or a background HTTP
                 call (ask)
    idle      -> a turn finished with nothing further to do
                 automatically (ask responses; code turns with no repo
                 to open a PR against) -- waits for the next message
    pr_open   -> a code turn finished, a PR exists, clock is ticking
                 towards sleep
    sleeping  -> idle past the timeout; polled for new PR comments
    stopped   -> interrupted from the UI mid-turn
    failed    -> the turn errored out; stays visible for the UI to
                 retry (send a new message) or close
    closed / archived -> scheduler ignores these entirely
"""

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from datetime import datetime, timezone

from . import agent_context, agent_settings, code_index, cron, github_tools, specs
from . import models as model_registry
from . import store
from .routing import route as route_turn
from .config import Settings
from .docker_runner import DockerRunner
from .github_client import GitHubClient

logger = logging.getLogger("factory.scheduler")

TERMINAL_STATUSES = {"closed", "archived"}

_chunk_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def relevant_code(settings: Settings, repo: str, task: str) -> str:
    """The repo's code chunks closest to the task, as markdown for the agent's
    first message. Empty when there's no index, no embedding model, or the
    model server can't embed the task; the turn goes ahead without it.
    """
    code_map = store.get_code_map(repo)
    if not code_map or not task.strip():
        return ""
    terms = code_index.query_terms(task)
    named = code_index.matching_files(code_map["map"], terms)
    files_section = (
        "Files whose names or definitions match the task:\n\n" + "\n".join(f"- {line}" for line in named) + "\n\n"
        if named else ""
    )
    if not code_map["chunk_count"] or not settings.embedding_model_id:
        return files_section.strip()
    cached = _chunk_cache.get(repo)
    if cached is None or cached[0] != code_map["updated_at"]:
        cached = (code_map["updated_at"], store.load_code_chunks(repo))
        _chunk_cache[repo] = cached
    try:
        [query] = code_index.embed(
            settings.local_model_host_base_url, settings.embedding_model_id,
            [f"search_query: {task[:2000]}"], timeout=30,
        )
    except Exception as exc:
        logger.warning("code search unavailable for %s: %s", repo, exc)
        return files_section.strip()
    return files_section + code_index.format_relevant(
        code_index.top_chunks(query, cached[1], k=8, terms=terms)
    )


PERSONA_PATH = Path(__file__).resolve().parent.parent / "images" / "agent" / "PERSONA.md"


def persona() -> str:
    """Bertha's character, for ask sessions' system prompt. Read each time,
    so an edit applies to the next reply."""
    try:
        return PERSONA_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def working_context(model: dict[str, Any]) -> int:
    """The context size OpenCode treats as full for this model, so it compacts
    there: the Working context setting, capped at what the model has loaded.
    """
    target = agent_settings.current()["working_context"]
    return min(target, model["context_length"]) if model.get("context_length") else target


BUSY_OR_QUEUED = {"running", "queued"}
# Turns Bertha starts to fix failing CI before handing it to you; reset when
# CI passes or you send a message.
CI_FIX_ATTEMPTS = 2
# A push with no checks after this long is taken as a repo without CI.
CI_GRACE_SECONDS = 600
# Review rounds per session: QC re-reviews Bertha's fixes to its findings
# until it passes or this many reviews have run.
MAX_REVIEW_ROUNDS = 3
# How often a closed-PR check (for review notes) and a merged-PR check (for
# wiki freshness) may call GitHub, per session and per repo.
CONTEXT_CHECK_SECONDS = 600


class Scheduler:
    def __init__(self, settings: Settings, runner: DockerRunner, gh: GitHubClient):
        self.settings = settings
        self.runner = runner
        self.gh = gh
        self._ask_clients: dict[int, httpx.Client] = {}
        self._lock = threading.Lock()
        # Set by app/main.py: rebuilds out-of-date wikis.
        self.wiki: Any = None
        # When each session's PR / each repo's merges were last checked.
        self._context_checked: dict[str, float] = {}
        # Notes are written by the model one at a time, off the tick thread.
        self._harvest_lock = threading.Lock()

    def tick_fast(self) -> None:
        """Everything local -- no GitHub calls, no external rate limit,
        so this runs on a short interval. A freshly created session
        (or one a user just messaged) launches on the next tick of
        *this* loop, not the slow one -- otherwise session creation
        would feel broken, waiting up to a full poll_interval_seconds
        before anything visibly happens.
        """
        self._run_due_schedules()
        self._enforce_deadlines()
        self._promote_pending_messages()
        self._start_queued()
        self._check_running()
        self._check_pr_open_idle()

    def tick_slow(self) -> None:
        """Everything that calls the GitHub API: issue discovery and
        polling sleeping sessions' PRs for new comments. Runs on
        `poll_interval_seconds`, same as before this split.
        """
        self._discover_new_issues()
        self._check_spec_reviews()
        self._check_ci()
        self._check_sleeping()
        self._harvest_reviews()
        self._refresh_wikis()

    def _routed(self, session: dict[str, Any], prompt: str, session_type: str) -> dict[str, Any]:
        """The session as this turn should see it: routed to the model that
        fits the task, unless the user pinned one. A changed model is stored
        on the session and logged with the reason.
        """
        task = {"session_type": session_type, "has_repo": bool(session["repo"]), "prompt": prompt}
        chosen, reason = route_turn(self.settings, session, task)
        if chosen is None or chosen["key"] == session["model"]:
            return session
        store.update_session(session["id"], model=chosen["key"])
        store.append_log(
            session["id"], "system",
            f"Routed to {chosen['key']} ({chosen['tier']}): {reason or 'the default'}",
        )
        return {**session, "model": chosen["key"]}

    # -- schedules -------------------------------------------------------

    def _run_due_schedules(self) -> None:
        now = time.time()
        for schedule in store.due_schedules(now):
            scheduled_for = schedule["next_run_at"]
            # The next time counts from now, not from the missed slot, so a run
            # missed while this machine was off happens once, not once per slot.
            try:
                upcoming = cron.next_run(schedule["cron"], schedule["timezone"],
                                         datetime.fromtimestamp(max(now, scheduled_for), tz=timezone.utc))
                store.update_schedule(schedule["id"], next_run_at=upcoming.timestamp())
            except cron.CronError as exc:
                store.update_schedule(schedule["id"], enabled=0, next_run_at=None)
                store.create_schedule_run(schedule["id"], "failed", scheduled_for,
                                          reason=f"schedule disabled: {exc}")
                continue
            self.start_schedule_run(schedule, scheduled_for)

    def start_schedule_run(self, schedule: dict[str, Any], scheduled_for: float) -> dict[str, Any]:
        """Starts one run of a schedule as a normal session, unless the previous
        run is still going, in which case this run is recorded as failed.
        Returns the run."""
        last = store.last_schedule_run(schedule["id"])
        if last and last["status"] == "running":
            previous = store.get_session(last["session_id"]) if last["session_id"] else None
            if previous and previous["status"] in ("running", "queued"):
                run_id = store.create_schedule_run(
                    schedule["id"], "failed", scheduled_for,
                    reason=f"the previous run (session {previous['ref']}) was still running",
                )
                return {"id": run_id, "status": "failed"}
            # Its session ended without closing the run (e.g. a restart); close it now.
            store.finish_schedule_run(last["session_id"], "failed", "ended without reporting")

        session_id = store.create_session(
            session_type=schedule["session_type"],
            repo=schedule["repo"],
            title=schedule["name"],
            pending_message=schedule["prompt"],
            model=schedule["model"],
            model_pinned=bool(schedule["model"]),
            github_tools=schedule["github_tools"],
            schedule_id=schedule["id"],
            deadline_at=time.time() + schedule["timeout_minutes"] * 60,
        )
        store.append_log(session_id, "system", f"Started by the schedule \"{schedule['name']}\"")
        store.append_log(session_id, "user_message", schedule["prompt"])
        run_id = store.create_schedule_run(schedule["id"], "running", scheduled_for, session_id=session_id)
        logger.info("schedule %s started session %s", schedule["id"], session_id)
        return {"id": run_id, "status": "running", "session_id": session_id}

    def _enforce_deadlines(self) -> None:
        """Stops scheduled runs that have gone past their timeout."""
        now = time.time()
        for status in ("running", "queued"):
            for session in store.list_sessions(status=status):
                deadline = session.get("deadline_at")
                if not deadline or deadline > now:
                    continue
                store.finish_schedule_run(session["id"], "timed_out", "went past the schedule's timeout")
                store.append_log(session["id"], "system", "Stopped: the scheduled run went past its timeout")
                if status == "running":
                    self.stop_turn(session)
                else:
                    store.update_session(session["id"], status="stopped", pending_message=None)
                store.update_session(session["id"], deadline_at=None, attention="failed")

    def _github_tools_env(self, session: dict[str, Any]) -> dict[str, str]:
        """A fresh key for this turn's GitHub tools. Every session with a repo
        gets them: dry runs to read, others also to change their own issue
        and pull request and push their branch, and sessions with GitHub tools
        turned on to change any issue (app/github_tools.py). The previous
        turn's key is revoked first; this one is revoked when the turn ends
        (see _settle and stop_turn)."""
        if not session.get("repo"):
            return {}
        scope = "read" if session.get("dry_run") else ("full" if session.get("github_tools") else "own")
        store.revoke_grants(session["id"])
        limits = agent_settings.current()
        key = store.create_grant(session["id"], session["repo"],
                                 limits["github_max_edits"], limits["github_max_closes"],
                                 scope=scope, max_pushes=limits["github_max_pushes"])
        host = "egress" if self.settings.egress_isolation else "host.docker.internal"
        base = f"http://{host}:{self.settings.tools_port}"
        return {"GITHUB_TOOLS_URL": f"{base}/mcp", "GITHUB_TOOLS_KEY": key,
                "CONTEXT_TOOLS_URL": f"{base}/context/mcp"}

    def _settle(self, session_id: int, status: str, attention: str | None, **fields: Any) -> None:
        """Ends a turn. `attention` says what the session is waiting on
        ("replied", "review" or "failed"), or None. Nothing is flagged when
        the user already sent a message while the turn ran, since that
        message is the next thing to act on. A failed turn is remembered, so
        the next one is routed up a tier.
        """
        current = store.get_session(session_id)
        if current and current["pending_message"]:
            attention = None
        store.revoke_grants(session_id)
        if current and current.get("schedule_id"):
            store.finish_schedule_run(session_id, "failed" if status == "failed" else "succeeded")
            # The run is over; later turns the user starts here have no deadline.
            fields["deadline_at"] = None
        store.update_session(
            session_id,
            status=status,
            attention=attention,
            last_turn_failed=1 if status == "failed" else 0,
            **fields,
        )

    # -- stop (called from an API handler, not the tick loop) -----------

    def stop_turn(self, session: dict[str, Any]) -> bool:
        """Best-effort interrupt of a running turn. Returns True if
        something was actually stopped.
        """
        if session["status"] != "running":
            return False
        if session["session_type"] == "ask":
            with self._lock:
                client = self._ask_clients.get(session["id"])
            if client:
                client.close()  # the worker thread's request raises; it marks "stopped"
            return client is not None
        if session["container_id"]:
            self.runner.remove(session["container_id"])
        store.update_session(session["id"], stream_log_id=None)
        store.revoke_grants(session["id"])
        if session.get("schedule_id"):
            store.finish_schedule_run(session["id"], "stopped")
        store.update_session(session["id"], status="stopped", container_id=None, pending_message=None)
        store.append_log(session["id"], "system", "Stopped from UI")
        return True

    # -- discovery ---------------------------------------------------

    def _discover_new_issues(self) -> None:
        for repo in store.list_repos():
            try:
                issues = self.gh.find_assigned_issues(repo["full_name"])
            except Exception:
                logger.exception("failed listing issues for %s", repo["full_name"])
                continue
            for issue in issues:
                if store.find_session_by_issue(repo["full_name"], issue["number"]):
                    continue
                self._create_code_session(repo["full_name"], issue)

    def _create_code_session(self, repo: str, issue: dict[str, Any]) -> int:
        branch = f"factory/issue-{issue['number']}"
        session_id = store.create_session(
            session_type="code",
            repo=repo,
            issue_number=issue["number"],
            title=issue["title"],
            branch_name=branch,
            pending_message=issue["body"] or issue["title"],
            # An issue labelled needs-spec gets a spec to review before any code.
            spec_first="needs-spec" in issue.get("labels", []),
        )
        store.append_log(
            session_id, "system", f"Picked up issue #{issue['number']}: {issue['title']}"
        )
        store.append_log(session_id, "user_message", issue["body"] or issue["title"])
        logger.info("session %s created for %s#%s", session_id, repo, issue["number"])
        return session_id

    # -- promotion / launch -------------------------------------------

    def _promote_pending_messages(self) -> None:
        """Any non-terminal, non-busy session with a pending_message is
        ready to run (or re-run): a fresh issue, a UI message, or a new
        PR comment picked up while sleeping all land here the same way.
        """
        for session in store.list_sessions():
            if session["status"] in TERMINAL_STATUSES | BUSY_OR_QUEUED:
                continue
            if session["pending_message"]:
                store.update_session(session["id"], status="queued")

    def _start_queued(self) -> None:
        for session in store.list_sessions(status="queued"):
            if session["session_type"] == "ask":
                self._launch_ask_turn(session)
            else:
                self._launch_code_turn(session)  # repo is optional; entrypoint.sh handles either

    # -- ask turns: no container, answered directly in a worker thread --

    def _launch_ask_turn(self, session: dict[str, Any]) -> None:
        # Not logged here -- whoever set pending_message (api_send_message,
        # api_new_session, etc.) already logged it at that point. Logging
        # it again here would duplicate it.
        session = self._routed(session, session.get("pending_message") or "", "ask")
        messages = self._build_ask_messages(session["id"])
        if not any(m["role"] == "user" for m in messages):
            # Nothing to answer -- don't call the model with only the
            # system prompt.
            store.update_session(session["id"], status="idle", pending_message=None)
            return
        store.update_session(
            session["id"],
            status="running",
            pending_message=None,
            turn_count=session["turn_count"] + 1,
        )
        client = httpx.Client(timeout=180.0)
        with self._lock:
            self._ask_clients[session["id"]] = client
        chosen = model_registry.resolve(self.settings, session.get("model"))
        model_id = chosen["model_id"] if chosen else self.settings.local_model_id
        thread = threading.Thread(
            target=self._run_ask_request,
            args=(session["id"], client, messages, model_id),
            daemon=True,
        )
        thread.start()

    def _build_ask_messages(self, session_id: int) -> list[dict[str, str]]:
        # The current prompt is already the latest "user_message" log
        # entry (logged by whoever set pending_message) by the time this
        # runs, so the whole conversation -- including it -- just comes
        # from the log. Appending it again here would send it twice.
        # Bertha's character (images/agent/PERSONA.md), which code turns get
        # as standing instructions too.
        character = persona()
        messages = [{"role": "system", "content": character}] if character else []
        for log in store.list_logs(session_id):
            if log["kind"] == "user_message":
                messages.append({"role": "user", "content": log["content"]})
            elif log["kind"] == "ask_response":
                messages.append({"role": "assistant", "content": log["content"]})
        return messages

    def _run_ask_request(
        self, session_id: int, client: httpx.Client, messages: list[dict[str, str]], model_id: str
    ) -> None:
        # Streamed: the reply's log entry is created at its first token and
        # rewritten as it grows, so the UI shows it being written.
        log_id: int | None = None
        text = ""
        saved_at = 0.0
        try:
            with client.stream(
                "POST",
                f"{self.settings.local_model_host_base_url}/chat/completions",
                json={"model": model_id, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                for delta in _stream_deltas(resp.iter_lines()):
                    text += delta
                    if log_id is None:
                        log_id = store.append_log(session_id, "ask_response", text)
                        saved_at = time.monotonic()
                    elif time.monotonic() - saved_at >= ASK_SAVE_INTERVAL:
                        store.set_log_content(log_id, text)
                        saved_at = time.monotonic()
            if log_id is None:
                store.append_log(session_id, "ask_response", text)
            else:
                store.set_log_content(log_id, text)
            self._settle(session_id, "idle", "replied", last_activity_at=time.time())
        except Exception as exc:
            # Either a real failure, or stop_turn() closed the client out
            # from under us -- can't cleanly tell which, so the log line
            # carries the detail and the status just reflects "not running
            # anymore" rather than guessing which one happened.
            if log_id is not None:
                store.set_log_content(log_id, text)  # keep what arrived before it ended
            current = store.get_session(session_id)
            if current and current["status"] == "running":
                store.append_log(session_id, "system", f"Ask turn ended: {exc}")
                store.update_session(session_id, status="stopped", last_activity_at=time.time())
        finally:
            with self._lock:
                self._ask_clients.pop(session_id, None)
            client.close()

    # -- code turns: a container against a persistent per-session volume -

    def _launch_code_turn(self, session: dict[str, Any]) -> None:
        # Not logged here -- whoever set pending_message already logged it
        # (api_send_message, api_new_session, _create_code_session,
        # _check_sleeping). Logging it again here would duplicate it.
        prompt = session["pending_message"] or session["title"] or ""
        session = self._routed(session, prompt, "code")
        # The ref keeps this unique, so a branch left over from an earlier
        # session can't block the checkout.
        branch_name = session["branch_name"] or f"factory/session-{session['ref']}"
        spec_env = self._spec_turn_env(session, prompt, branch_name)
        chosen = model_registry.resolve(self.settings, session.get("model"))
        opencode_model = (
            f"{self.settings.local_model_provider}/{chosen['key']}"
            if chosen else self.settings.opencode_model_ref
        )
        env = {
            "REPO": session["repo"] or "",
            "GITHUB_TOKEN": self.settings.github_token,
            "BRANCH_NAME": branch_name,
            "PROMPT": spec_env.pop("message", prompt),
            "OPENCODE_SESSION_ID": session["harness_session_id"] or "",
            "OPENCODE_MODEL": opencode_model,
            # What exists about the repo -- the wiki's summary and pages, saved
            # notes -- loaded as standing instructions (like CLAUDE.md); the
            # context tools read the rest.
            "REPO_CONTEXT": agent_context.repo_context(session["repo"]) if session["repo"] else "",
            # Code a search of the repo found for this task; added to the first message.
            "RELEVANT_CODE": relevant_code(self.settings, session["repo"], prompt) if session["repo"] else "",
            # How far this turn may run: steps, continuations, timeout, token budget.
            **agent_settings.env_for_turn(),
            # A scheduled run is bounded by the schedule's own timeout, not the
            # per-run setting: its run may use whatever time is left.
            **({"AGENT_RUN_TIMEOUT_SECONDS": str(max(60, int(session["deadline_at"] - time.time())))}
               if session.get("deadline_at") else {}),
            # Every enabled model, so OpenCode knows the full set it can switch to.
            "MODELS_JSON": json.dumps([
                {"key": m["key"], "model_id": m["model_id"],
                 "context": working_context(m)}
                for m in store.list_models() if m["enabled"]
            ]),
            "LOCAL_MODEL_PROVIDER": self.settings.local_model_provider,
            "LOCAL_MODEL_BASE_URL": self.settings.local_model_base_url,
            "LOCAL_MODEL_KEY": self.settings.local_model_key,
            "LOCAL_MODEL_ID": self.settings.local_model_id,
            "NO_PUSH": "1" if session.get("dry_run") else "",
            # A code review session speaks as QC and reviews its parent's PR.
            **self._review_env(session),
            **self._github_tools_env(session),
            "BOT_NAME": self.settings.bot_name,
            "BOT_EMAIL": self.settings.bot_email,
            # Skills enabled for this repo, written into /data/skills by the entrypoint.
            "SKILLS_JSON": json.dumps(
                store.list_repo_skills(session["repo"]) if session["repo"] else []
            ),
            "PR_NUMBER": str(session["pr_number"] or ""),
            "PR_TITLE": (
                f"{session['title']} (closes #{session['issue_number']})"
                if session["issue_number"] else (session["title"] or branch_name)
            ),
            "PR_BODY": (
                f"Automated changes for #{session['issue_number']}.\n\n"
                f"Closes #{session['issue_number']}"
                if session["issue_number"] else "Automated changes from factory agent."
            ),
            **spec_env,
        }
        try:
            container_id = self.runner.start_turn(
                session["id"], store.workspace_of(session), env,
                limits=agent_settings.container_limits(),
                model_port=self.settings.model_host_port[1],
            )
        except Exception:
            logger.exception("failed starting container for session %s", session["id"])
            store.update_session(session["id"], status="failed", attention="failed")
            store.append_log(session["id"], "system", "Failed to start container")
            return
        store.update_session(
            session["id"],
            stream_log_id=None,
            status="running",
            container_id=container_id,
            pending_message=None,
            branch_name=branch_name,
            turn_count=session["turn_count"] + 1,
        )

    def _check_running(self) -> None:
        for session in store.list_sessions(status="running"):
            if session["session_type"] != "code" or not session["container_id"]:
                continue  # ask turns resolve themselves from their own thread
            try:
                container = self.runner.poll(session["container_id"])
            except Exception:
                logger.exception("lost track of container for session %s", session["id"])
                continue
            if container.status != "exited":
                self._stream_output(session)
                continue
            self._finish_code_turn(session, container)

    def _stream_output(self, session: dict[str, Any]) -> None:
        """Copies the container's output into the session log as it's
        produced, so the transcript moves while the agent works. Only whole
        lines are copied, because the output is JSON events one per line.
        """
        try:
            logs = self.runner.logs(session["container_id"])
        except Exception:
            return  # the next tick tries again
        cut = logs.rfind("\n")
        if cut < 0:
            return
        self._write_turn_output(session, logs[: cut + 1])

    def _write_turn_output(self, session: dict[str, Any], output: str) -> None:
        """Sets this turn's output entry to the container's output so far.
        The entry is remembered on the session row, and its content replaced
        rather than appended to, so an API restart mid-turn can't duplicate or
        drop output."""
        log_id = session.get("stream_log_id")
        if log_id:
            store.set_log_content(log_id, output)
        else:
            log_id = store.append_log(session["id"], "agent_output", output)
            store.update_session(session["id"], stream_log_id=log_id)
            session["stream_log_id"] = log_id

    def _finish_code_turn(self, session: dict[str, Any], container: Any) -> None:
        logs = self.runner.logs(session["container_id"])
        self._write_turn_output(store.get_session(session["id"]) or session, logs)
        store.update_session(session["id"], stream_log_id=None)
        exit_code = container.attrs.get("State", {}).get("ExitCode", 1)
        self.runner.remove(session["container_id"])
        # Fresh: the agent may have opened its PR with its tools mid-turn.
        session = store.get_session(session["id"]) or session

        pr_number = session["pr_number"]
        harness_session_id = session["harness_session_id"]
        for line in logs.splitlines():
            if line.startswith("FACTORY_PR_NUMBER="):
                value = line.split("=", 1)[1].strip()
                if value.isdigit():
                    pr_number = int(value)
            elif line.startswith("FACTORY_OPENCODE_SESSION_ID="):
                value = line.split("=", 1)[1].strip()
                if value:
                    harness_session_id = value

        agent_failed = "FACTORY_AGENT_FAILED=1" in logs
        # A turn that changed nothing and said nothing did no work, however
        # cleanly it exited (a model that loses its way can end like that).
        silent = (exit_code == 0 and not agent_failed and not _final_message(logs)
                  and ("FACTORY_NO_CHANGES=1" in logs or not session["repo"]))
        loop = next((line.split("=", 1)[1].strip() for line in logs.splitlines()
                     if line.startswith("FACTORY_LOOP=")), "")
        if loop:
            store.append_log(session["id"], "system",
                             f"Stopped the agent retrying something that kept failing: {loop}")
        if silent:
            store.append_log(session["id"], "system",
                             "The agent finished without changing anything or giving an answer")
        step_limit = "FACTORY_STEP_LIMIT=1" in logs
        # Only the first failure in a row is retried on another model.
        retry_allowed = not session.get("last_turn_failed")
        if "FACTORY_RUN_TIMED_OUT=1" in logs:
            store.append_log(session["id"], "system", "The agent run went past its time limit and was stopped")
            if session.get("schedule_id"):
                # Recorded before _settle, which would otherwise record a plain failure.
                store.finish_schedule_run(session["id"], "timed_out", "the agent run went past the time left")
        if exit_code != 0 or agent_failed or silent:
            new_status = "failed"
        elif session["repo"]:
            # A PR means the work is waiting on review. Without one the turn
            # still finished cleanly -- the agent changed nothing, or the PR
            # couldn't be opened and will be retried on the next turn -- so
            # it's idle, not failed.
            new_status = "pr_open" if pr_number else "idle"
        else:
            # no repo attached: nothing to push, so "ran cleanly" is success
            new_status = "idle"
        attention = {"failed": "failed", "pr_open": "review", "idle": "replied"}.get(new_status)
        # A turn that pushed to its PR: watch the CI that push sets off.
        watch_ci = ({"ci_sha": ""} if new_status == "pr_open" and "FACTORY_PUSHED=1" in logs
                    and not session.get("dry_run") else {})
        self._settle(
            session["id"],
            new_status,
            attention,
            container_id=None,
            pr_number=pr_number,
            harness_session_id=harness_session_id,
            last_activity_at=time.time(),
            **watch_ci,
        )
        logger.info(
            "session %s turn finished: exit=%s pr=%s -> %s",
            session["id"], exit_code, pr_number, new_status,
        )
        if session["repo"]:
            try:
                self._report_push(store.get_session(session["id"]), logs)
            except Exception:
                logger.exception("failed reporting session %s's push", session["id"])
        if session.get("spec_phase") and session["repo"]:
            try:
                self._after_spec_turn(store.get_session(session["id"]), logs, new_status)
            except Exception:
                logger.exception("failed updating the spec PR for session %s", session["id"])
        if session.get("review_of"):
            try:
                self._after_review(store.get_session(session["id"]) or session, logs)
            except Exception:
                logger.exception("failed handling review session %s", session["id"])
        elif session.get("review_fix_pending"):
            self._after_review_fix(store.get_session(session["id"]) or session,
                                   pushed=new_status == "pr_open" and "FACTORY_PUSHED=1" in logs)
        elif new_status == "pr_open" and "FACTORY_PUSHED=1" in logs and self._auto_review_due(session):
            try:
                self.start_review(store.get_session(session["id"]) or session, by="automatic review")
            except Exception:
                logger.exception("failed starting a review for session %s", session["id"])
        if new_status == "failed" and "FACTORY_CONTEXT_TOO_SMALL=1" in logs:
            self._retry_on_another_model(
                store.get_session(session["id"]),
                "its context is too small for this turn",
                "Pick a bigger model, or load this one with a larger context in the model server.",
            )
        elif new_status == "failed" and "FACTORY_MODEL_UNAVAILABLE=1" in logs:
            self._retry_on_another_model(
                store.get_session(session["id"]),
                "the model server couldn't load it",
                "Pick a model that's loaded, or free memory in the model server.",
            )
        elif new_status == "failed" and (silent or loop or step_limit) and retry_allowed:
            self._retry_on_another_model(
                store.get_session(session["id"]),
                "it kept retrying something that fails" if loop
                else "it ran out of steps before finishing" if step_limit else "it lost its way on this task",
                "Send a message to try again, or pick another model.",
            )

    def _retry_on_another_model(self, session: dict[str, Any], problem: str, advice: str) -> None:
        """A turn whose model couldn't take the request, or that went nowhere
        on it, is run again on another model, excluding the one that failed.
        A pinned session isn't moved; the failure says why instead.
        """
        failed_model = session["model"]
        if session["model_pinned"]:
            store.append_log(session["id"], "system", f"{failed_model} can't run this turn: {problem}. {advice}")
            return
        prompt = next(
            (l["content"] for l in reversed(store.list_logs(session["id"]))
             if l["kind"] in ("user_message", "pr_comment", "ci_failure")),
            session["title"] or "",
        )
        task = {"session_type": "code", "has_repo": bool(session["repo"]), "prompt": prompt}
        chosen, _reason = route_turn(
            self.settings, {**session, "last_turn_failed": 1}, task,
            exclude=frozenset({failed_model} if failed_model else ()),
        )
        if chosen is None or chosen["key"] == failed_model:
            store.append_log(
                session["id"], "system",
                f"{failed_model} can't run this turn ({problem}), and no other enabled model can "
                "take it. Load a model with enough context, or lower the minimum in Settings.",
            )
            return
        store.update_session(session["id"], model=chosen["key"], last_turn_failed=1)
        store.append_log(
            session["id"], "system",
            f"Retrying on {chosen['key']}: {failed_model} can't run this turn ({problem})",
        )
        # Queues the same task again; the next tick launches it.
        store.set_pending_message(session["id"], prompt)

    # -- idle / sleep (code sessions with a PR only) --------------------

    def _check_pr_open_idle(self) -> None:
        cutoff = time.time() - self.settings.sleep_after_minutes * 60
        for session in store.list_sessions(status="pr_open"):
            if session["last_activity_at"] <= cutoff:
                store.update_session(session["id"], status="sleeping")
                store.append_log(session["id"], "system", "Sleeping after idle timeout")

    def _check_sleeping(self) -> None:
        for session in store.list_sessions(status="sleeping"):
            if not session["pr_number"] or not session["repo"]:
                continue
            if session.get("spec_phase") == "review":
                continue  # _check_spec_reviews reads this PR's comments
            try:
                # Comments since the session went quiet. Sessions from before
                # the time cursor start from their last activity, so comments
                # they already passed over don't wake them now.
                comments = self.gh.new_pr_comments(
                    session["repo"], session["pr_number"], session["last_seen_comment_id"],
                    since=session.get("comments_seen_at") or session["last_activity_at"],
                )
            except Exception:
                logger.exception("failed polling PR comments for session %s", session["id"])
                continue
            if not comments:
                continue
            store.update_session(session["id"], comments_seen_at=max(c["at"] for c in comments))
            # Only people who can write to the repo can set the agent to work.
            comments = [c for c in comments if self.gh.can_write(session["repo"], c["author"])]
            if not comments:
                continue
            combined = "\n\n".join(f"@{c['author']}: {c['body']}" for c in comments)
            store.set_pending_message(session["id"], combined)
            store.append_log(session["id"], "pr_comment", combined)
            logger.info("session %s woken by PR comment(s)", session["id"])

    # -- what the end of a turn pushed ----------------------------------

    def _report_push(self, session: dict[str, Any], logs: str) -> None:
        """Says in the session when the branch couldn't be pushed, and posts
        workflow changes, which are never pushed, on the PR for you to apply."""
        values = {}
        for line in logs.splitlines():
            if line.startswith(("FACTORY_PUSH_FAILED=", "FACTORY_WORKFLOW_HELD=", "FACTORY_WORKFLOW_PATCH=")):
                key, value = line.split("=", 1)
                values[key] = value.strip()
        if "FACTORY_BRANCH_MERGED=1" in logs:
            store.append_log(session["id"], "system",
                             "The branch on GitHub had changed alongside this session's work, so Bertha merged "
                             "the two (this session's side wins where they overlap)")
        if "FACTORY_BRANCH_DIVERGED=1" in logs:
            store.append_log(session["id"], "system",
                             "The branch on GitHub has changes that can't be merged with this session's work "
                             "automatically; resolve them on the branch, then send a message")
        if "FACTORY_PUSH_FAILED" in values:
            reason = values["FACTORY_PUSH_FAILED"] or "GitHub refused it"
            store.append_log(session["id"], "system",
                             f"Bertha couldn't push the branch: {reason}. The work is committed in the session's "
                             "workspace; send a message to try again.")
        held = values.get("FACTORY_WORKFLOW_HELD")
        if not held:
            return
        try:
            patch = base64.b64decode(values.get("FACTORY_WORKFLOW_PATCH", "")).decode(errors="replace")
        except ValueError:
            patch = ""
        where = f"PR #{session['pr_number']}" if session["pr_number"] else "the session (no PR yet)"
        store.append_log(session["id"], "system",
                         f"Left workflow changes out of the push ({held.strip()}): agents can't change CI "
                         f"workflows. The change is posted on {where} for you to apply.")
        if not session["pr_number"]:
            store.append_log(session["id"], "system", f"Workflow change:\n\n```diff\n{patch[:20_000]}\n```")
            return
        shown = patch if len(patch) <= 50_000 else patch[:50_000] + "\n… (truncated)"
        self.gh.comment_pr(session["repo"], session["pr_number"], (
            "**Workflow changes for you to apply.** The agent changed CI workflows, which Bertha never "
            "pushes: a workflow an agent wrote would run with this repo's secrets before anyone reviewed it. "
            "The rest of the change is on this branch. If this looks right, apply it on the branch and push "
            f"(`git apply` with the diff below):\n\n```diff\n{shown}\n```"
        ))

    # -- CI on a session's pull request ---------------------------------

    def _check_ci(self) -> None:
        """Watches CI on what sessions push. A failure starts a turn to fix
        it, up to CI_FIX_ATTEMPTS in a row; after that the session waits on
        you. A spec-first PR is marked ready for review once CI passes."""
        for session in store.list_sessions():
            if (session.get("ci_sha") is None or not session["pr_number"] or not session["repo"]
                    or session.get("dry_run") or session["status"] not in ("pr_open", "sleeping", "idle")
                    or session.get("spec_phase") in ("drafting", "review", "abandoned")):
                continue
            try:
                report = github_tools.ci_failure_report(session["repo"], session["pr_number"])
            except github_tools.ToolError as exc:
                if str(exc) == github_tools.CI_PERMISSION_HELP:
                    store.append_log(session["id"], "system", f"Not watching CI on PR #{session['pr_number']}: {exc}")
                    store.update_session(session["id"], ci_sha=None)
                    if session.get("spec_phase") == "built":
                        # Nothing to wait for: ready now, saying CI wasn't checked.
                        self._ci_passed(session, {"state": "unchecked", "draft": True})
                else:
                    logger.warning("couldn't read CI for session %s: %s", session["id"], exc)
                continue
            except Exception:
                logger.exception("failed reading CI for session %s", session["id"])
                continue
            if report["pr_state"] != "open":
                store.update_session(session["id"], ci_sha=None)  # merged or closed: stop watching
                continue
            if report["sha"] == session["ci_sha"] or report["state"] == "pending":
                continue
            if report["state"] == "none" and time.time() - session["last_activity_at"] < CI_GRACE_SECONDS:
                continue  # CI may not have picked the push up yet
            store.update_session(session["id"], ci_sha=report["sha"])
            if report["state"] == "failed":
                self._ci_failed(session, report)
            else:
                self._ci_passed(session, report)

    def _ci_failed(self, session: dict[str, Any], report: dict[str, Any]) -> None:
        if not session.get("ci_failed_sha"):
            # The first failing commit: when CI passes again, the change since
            # then is the fix, and becomes a CI note (_ci_passed).
            store.update_session(session["id"], ci_failed_sha=report["sha"])
        names = ", ".join(f"`{c['name']}`" for c in report["failed"])
        attempts = session.get("ci_fix_attempts") or 0
        if attempts >= CI_FIX_ATTEMPTS:
            store.append_log(session["id"], "system",
                             f"CI is still failing on PR #{session['pr_number']} ({names}) after {attempts} "
                             "automatic fixes, so it's over to you")
            store.update_session(session["id"], attention="failed")
            if session.get("spec_phase") == "built":
                self._spec_pr_update(session, "ci_failing")
            return
        jobs = "\n\n".join(
            f"### `{c['name']}` ({c['conclusion']}, job {c['id']})\n\n```\n{c['log'][-6000:]}\n```"
            for c in report["failed"])
        message = (
            f"CI failed on this session's pull request #{session['pr_number']} "
            f"(commit {report['sha'][:7]}): {names}.\n\n{jobs}\n\n"
            "Find the cause and fix it, then check your fix the way CI does. If the failure isn't caused by "
            "this branch's changes (a flaky test, or something already broken on the base branch), don't "
            "change unrelated code to hide it: say so in your report."
        )
        store.update_session(session["id"], ci_fix_attempts=attempts + 1)
        store.set_pending_message(session["id"], message)
        store.append_log(session["id"], "ci_failure", message)
        logger.info("session %s: CI failed, fix attempt %s", session["id"], attempts + 1)

    def _ci_passed(self, session: dict[str, Any], report: dict[str, Any]) -> None:
        if session.get("ci_fix_attempts"):
            store.append_log(session["id"], "system", f"CI passes on PR #{session['pr_number']} now")
        store.update_session(session["id"], ci_fix_attempts=0, ci_failed_sha=None)
        if session.get("ci_failed_sha") and report.get("sha"):
            self._harvest_in_background(self._harvest_ci_fix, session, session["ci_failed_sha"], report["sha"])
        if session.get("spec_phase") == "built" and report["draft"]:
            try:
                self.gh.mark_ready(session["repo"], session["pr_number"])
            except Exception:
                logger.exception("failed marking session %s's PR ready", session["id"])
                return
            store.append_log(session["id"], "system",
                             {"passed": "CI passed", "unchecked": "CI couldn't be checked"}.get(
                                 report["state"], "CI has no checks")
                             + f"; PR #{session['pr_number']} is marked ready for review")
            self._spec_pr_update(session, "built_unchecked" if report["state"] == "unchecked" else "built")

    # -- code review: QC checks Bertha's pull request in its own session -----

    def _review_env(self, session: dict[str, Any]) -> dict[str, str]:
        if not session.get("review_of"):
            return {}
        parent = store.get_session(session["review_of"]) or {}
        return {"PERSONA_FILE": "QC.md", "REVIEW_OF_PR": str(parent.get("pr_number") or "")}

    def _auto_review_due(self, session: dict[str, Any]) -> bool:
        """Once per session, after Bertha first pushes to its PR, when the
        repo has automatic review on. Her fixes aren't re-reviewed."""
        repo = store.get_repo_by_name(session["repo"]) if session.get("repo") else None
        return bool(repo and repo.get("auto_review") and not session.get("reviews_run")
                    and not session.get("dry_run") and session.get("spec_phase") not in ("drafting", "review"))

    def review_in_progress(self, session: dict[str, Any]) -> bool:
        review = store.get_session(session["review_session_id"]) if session.get("review_session_id") else None
        return bool(review and review["status"] in BUSY_OR_QUEUED)

    def start_review(self, session: dict[str, Any], by: str) -> dict[str, Any]:
        """A new session in which QC reviews this session's PR: its own
        container and conversation, read-only, ideally on a different model
        from the one that wrote the change. Returns the review session."""
        if not session.get("pr_number") or not session.get("repo") or not session.get("branch_name"):
            raise ValueError("this session has no pull request to review")
        if self.review_in_progress(session):
            raise ValueError("a review of this pull request is already running")
        base = "the default branch"
        try:
            with github_tools._client() as client:
                pr = github_tools._check(client.get(f"/repos/{session['repo']}/pulls/{session['pr_number']}"))
            base = pr["base"]["ref"]
        except Exception:
            logger.warning("couldn't read PR #%s's base branch", session["pr_number"])
        prompt = (
            f"Pull request #{session['pr_number']} in {session['repo']}, branch {session['branch_name']}, "
            f"base branch {base} (diff it against origin/{base}).\n\n"
            f"The task it was built for:\n\n{self._original_task(session)}"
        )
        review_id = store.create_session(
            session_type="code", repo=session["repo"], title=f"Review of PR #{session['pr_number']}",
            branch_name=session["branch_name"], pending_message=prompt, model=self._reviewer_model(session),
            model_pinned=True, dry_run=True,
        )
        store.update_session(review_id, review_of=session["id"])
        store.append_log(review_id, "system", f"Started by {by}: QC reviews PR #{session['pr_number']}")
        store.append_log(review_id, "user_message", prompt)
        review = store.get_session(review_id) or {}
        store.update_session(session["id"], review_session_id=review_id,
                             reviews_run=(session.get("reviews_run") or 0) + 1)
        store.append_log(session["id"], "system",
                         f"QC is reviewing PR #{session['pr_number']} in session {review.get('ref')} ({by})")
        return review

    def _reviewer_model(self, session: dict[str, Any]) -> str | None:
        """For a second opinion, the strongest enabled model that didn't write
        the change, as long as it's no weaker than the one that did; otherwise
        the strongest enabled model, which may be the same one (the review is
        still its own session and conversation)."""
        order = {"strong": 0, "standard": 1, "light": 2}
        enabled = sorted((m for m in store.list_models() if m["enabled"]), key=lambda m: order.get(m["tier"], 3))
        author = next((m for m in enabled if m["key"] == session.get("model")), None)
        floor = order.get(author["tier"], 3) if author else 3
        others = [m for m in enabled if m["key"] != session.get("model") and order.get(m["tier"], 3) <= floor]
        chosen = (others or enabled or [None])[0]
        return chosen["key"] if chosen else session.get("model")

    def _after_review(self, review: dict[str, Any], logs: str) -> None:
        """Posts QC's findings on the reviewed PR, and sets Bertha to fix them."""
        parent = store.get_session(review["review_of"])
        if not parent or not parent.get("pr_number"):
            return
        number = parent["pr_number"]
        report = parse_review(_final_message(logs))
        if report is None:
            store.append_log(parent["id"], "system",
                             f"QC's review (session {review['ref']}) ended without a report, so nothing was posted")
            return
        findings = report["findings"]
        try:
            self.gh.comment_pr(parent["repo"], number, review_comment(parent, report))
        except Exception:
            logger.exception("failed posting QC's review on PR #%s", number)
        if not findings:
            store.append_log(parent["id"], "system", f"QC passed PR #{number}: {report['summary']}")
            return
        message = (
            f"QC reviewed PR #{number} and found {len(findings)} problem{'s' if len(findings) != 1 else ''}. "
            "Fix each one, run the checks again, and push. If a finding is wrong, don't change code for it: "
            "say why in your report.\n\n" + format_findings(parent, findings)
        )
        pending = parent.get("pending_message")
        store.set_pending_message(parent["id"], f"{pending}\n\n{message}" if pending else message)
        store.update_session(parent["id"], review_fix_pending=1)
        store.append_log(parent["id"], "review_findings", message)

    def _after_review_fix(self, session: dict[str, Any], pushed: bool) -> None:
        """Bertha's turn fixing QC's findings has finished. A pushed fix is
        reviewed again, up to MAX_REVIEW_ROUNDS reviews, since a fix can
        bring its own bugs; a turn that pushed nothing (she disputed the
        findings, say) isn't."""
        store.update_session(session["id"], review_fix_pending=0)
        if not pushed:
            return
        rounds = session.get("reviews_run") or 0
        if rounds >= MAX_REVIEW_ROUNDS:
            store.append_log(session["id"], "system",
                             f"QC has reviewed PR #{session['pr_number']} {rounds} times, so Bertha's latest fixes "
                             "aren't reviewed again automatically. Run code review for another pass.")
            return
        try:
            self.start_review(session, by=f"re-review of Bertha's fixes, round {rounds + 1} of {MAX_REVIEW_ROUNDS}")
        except Exception:
            logger.exception("failed re-reviewing session %s", session["id"])

    # -- agent context: notes from sessions, wiki freshness (app/agent_context.py)

    def _due(self, key: str) -> bool:
        now = time.time()
        if now - self._context_checked.get(key, 0) < CONTEXT_CHECK_SECONDS:
            return False
        self._context_checked[key] = now
        return True

    def _harvest_in_background(self, work: Callable[..., None], *args: Any) -> None:
        """Runs a note harvest on its own thread, one at a time: each is a
        model call that can take minutes."""
        def run() -> None:
            with self._harvest_lock:
                try:
                    work(*args)
                except Exception:
                    logger.exception("couldn't write agent context notes")
        threading.Thread(target=run, name="factory-context-notes", daemon=True).start()

    def _harvest_reviews(self) -> None:
        """Reads reviewers' comments on each session's PR once it's merged or
        closed, for review notes."""
        for session in store.list_sessions():
            if (not session["pr_number"] or not session["repo"] or session.get("dry_run")
                    or session.get("context_harvested_at") or session["status"] in BUSY_OR_QUEUED
                    or not self._due(f"pr:{session['id']}")):
                continue
            try:
                state, merged = self.gh.pr_state(session["repo"], session["pr_number"])
            except Exception:
                logger.exception("couldn't read PR state for session %s", session["id"])
                continue
            if state == "closed":
                store.update_session(session["id"], context_harvested_at=time.time())
                self._harvest_in_background(self._harvest_review, session, merged)

    def _harvest_review(self, session: dict[str, Any], merged: bool) -> None:
        comments = self.gh.new_pr_comments(session["repo"], session["pr_number"], 0, since=0.0)
        # Only people who can write to the repo set its rules.
        comments = [c for c in comments if self.gh.can_write(session["repo"], c["author"])]
        made = agent_context.harvest_review_notes(self.settings, session, comments, merged)
        if made:
            store.append_log(session["id"], "system",
                             f"{made} note{'s' if made != 1 else ''} from the review of PR #{session['pr_number']} "
                             "are waiting in the repo's Context tab")

    def _harvest_ci_fix(self, session: dict[str, Any], failed_sha: str, fixed_sha: str) -> None:
        failure = next((l["content"] for l in reversed(store.list_logs(session["id"]))
                        if l["kind"] == "ci_failure"), "")
        if not failure:
            return
        diff = github_tools.compare_diff(session["repo"], failed_sha, fixed_sha)
        if agent_context.harvest_ci_note(self.settings, session, failure, diff, failed_sha, fixed_sha):
            store.append_log(session["id"], "system", "A note on this CI fix is waiting in the repo's Context tab")

    def _refresh_wikis(self) -> None:
        """Marks a wiki out of date when a PR merges into its repo's default
        branch, and rebuilds an out-of-date wiki once its last build is older
        than the Wiki refresh setting."""
        days = agent_settings.current()["wiki_refresh_days"]
        for index in store.list_wiki_indexes():
            repo = index["repo"]
            if index["status"] == "indexing" or not index.get("commit_sha"):
                continue
            if self._due(f"merges:{repo}"):
                since = index.get("merges_checked_at") or index.get("last_indexed_at") or index["created_at"]
                try:
                    merged = self.gh.merged_prs_since(repo, since)
                except Exception:
                    logger.exception("couldn't list merged PRs for %s", repo)
                    merged = []
                if merged:
                    first = merged[-1]
                    store.mark_wiki_dirty(repo, f"PR #{first['number']} was merged", first["at"])
                store.upsert_wiki_index(repo, merges_checked_at=time.time())
                index = store.get_wiki_index(repo) or index
            if (index.get("dirty_since") and self.wiki is not None
                    and time.time() - (index.get("last_indexed_at") or 0) >= days * 86400):
                if self.wiki.start(repo):
                    logger.info("rebuilding the out-of-date wiki for %s (%s)", repo, index.get("dirty_reason"))

    # -- spec-first sessions (app/specs.py) -----------------------------

    def _original_task(self, session: dict[str, Any]) -> str:
        first = next((l for l in store.list_logs(session["id"]) if l["kind"] == "user_message"), None)
        return first["content"] if first else session["title"] or ""

    def _spec_turn_env(self, session: dict[str, Any], prompt: str, branch: str) -> dict[str, str]:
        """What a spec-first session's turn adds to the container's environment.
        Drafting and revising are spec turns: the message is Bertha's own and
        only the spec file is kept. A build turn is a normal turn, whose
        message (set at approval) points at the approved spec."""
        phase = session.get("spec_phase") if session["repo"] else None
        if phase not in ("drafting", "review"):
            return {}
        path = session.get("spec_path") or specs.spec_path(session)
        if not session.get("spec_path"):
            store.update_session(session["id"], spec_path=path)
            session["spec_path"] = path
        if phase == "drafting":
            task = self._original_task(session)
            if prompt.strip() and prompt.strip() != task.strip():
                task += f"\n\nAdded by the user since:\n{prompt}"
            message = specs.draft_message(task, path, branch)
        else:
            message = specs.revise_message(prompt, path)
        return {
            "message": message,
            "SPEC_MODE": "draft",
            "SPEC_PATH": path,
            "COMMIT_TITLE": f"Spec: {session['title'] or path}",
        }

    def _after_spec_turn(self, session: dict[str, Any], logs: str, status: str) -> None:
        """Moves a spec-first session on after a turn, and brings the PR's label
        and status line up to date."""
        phase = session["spec_phase"]
        path = session.get("spec_path") or ""
        for line in logs.splitlines():
            if line.startswith("FACTORY_SPEC_DISCARDED="):
                store.append_log(session["id"], "system",
                                 "The spec turn changed files other than the spec; those changes were "
                                 f"discarded: {line.split('=', 1)[1].strip()}")
        if "FACTORY_SPEC_MISSING=1" in logs:
            store.append_log(session["id"], "system", f"The turn ended without writing the spec to {path}")
        if phase in ("drafting", "review"):
            if status == "failed":
                if phase == "review":
                    self._spec_pr_update(session, "review")
                return
            # A message sent during the turn is the next thing to act on, as in _settle.
            fields: dict[str, Any] = {"spec_phase": "review",
                                      "attention": None if session["pending_message"] else "spec"}
            if not session.get("comments_seen_at"):
                fields["comments_seen_at"] = time.time()
            store.update_session(session["id"], **fields)
            session = {**session, **fields}
            if phase == "review" and session["pr_number"] and not session.get("dry_run"):
                reply = _final_message(logs) or "Updated the spec."
                self.gh.comment_pr(session["repo"], session["pr_number"],
                                   f"Updated the spec ([`{path}`]({self._spec_url(session)})).\n\n{reply}")
            self._spec_pr_update(session, "review")
        elif phase == "building":
            if status == "failed":
                self._spec_pr_update(session, "build_failed")
                return
            # Ready for review once CI passes: _check_ci marks it ready then.
            store.update_session(session["id"], spec_phase="built", ci_sha="")
            self._spec_pr_update(session, "checking")

    def _spec_url(self, session: dict[str, Any]) -> str:
        return f"https://github.com/{session['repo']}/blob/{session['branch_name']}/{session['spec_path']}"

    def _spec_pr_update(self, session: dict[str, Any], state: str) -> None:
        """The PR's spec label and status line for `state` (a phase, or
        revising / build_failed). Best effort: GitHub being down mustn't
        stop the session moving on."""
        if not session.get("pr_number") or session.get("dry_run"):
            return
        repo, pr = session["repo"], session["pr_number"]
        label = {"review": "spec-review", "revising": "spec-review",
                 "building": "building", "build_failed": "building",
                 "checking": "building", "ci_failing": "building"}.get(state)
        line = specs.status_line(state, session.get("spec_path") or "", repo, session["branch_name"] or "")
        try:
            self.gh.set_pr_labels(repo, pr, label, specs.LABELS)
            self.gh.set_pr_body(repo, pr, lambda body: specs.with_status(body, line))
        except Exception:
            logger.exception("failed updating spec status on %s#%s", repo, pr)

    def _check_spec_reviews(self) -> None:
        """Acts on new comments on specs under review. Only people who can
        write to the repo steer a session; anyone else's comment is noted
        in the session and otherwise ignored."""
        for session in store.list_sessions():
            if (session.get("spec_phase") != "review" or not session["pr_number"] or not session["repo"]
                    or session["status"] in TERMINAL_STATUSES | BUSY_OR_QUEUED):
                continue
            since = session.get("comments_seen_at") or session["created_at"]
            try:
                comments = self.gh.new_pr_comments(session["repo"], session["pr_number"], 0, since=since)
            except Exception:
                logger.exception("failed reading spec comments for session %s", session["id"])
                continue
            if not comments:
                continue
            store.update_session(session["id"], comments_seen_at=max(c["at"] for c in comments))
            trusted = []
            for c in comments:
                if self.gh.can_write(session["repo"], c["author"]):
                    trusted.append(c)
                    store.append_log(session["id"], "pr_comment", f"@{c['author']}: {c['body']}")
                else:
                    store.append_log(session["id"], "system",
                                     f"Ignored a comment from @{c['author']}: they can't write to {session['repo']}")
            commands = {specs.command_of(c["body"]): c for c in trusted if specs.command_of(c["body"])}
            feedback = [c for c in trusted if not specs.command_of(c["body"])]
            if specs.ABANDON_COMMAND in commands:
                self.abandon_spec(session, f"@{commands[specs.ABANDON_COMMAND]['author']}")
            elif specs.APPROVE_COMMAND in commands:
                self.approve_spec(session, f"@{commands[specs.APPROVE_COMMAND]['author']}", feedback)
            elif feedback:
                store.set_pending_message(session["id"], "\n\n".join(specs.describe_comment(c) for c in feedback))
                self._spec_pr_update(session, "revising")

    def approve_spec(self, session: dict[str, Any], by: str, comments: list[dict[str, Any]] | None = None,
                     from_ui: bool = False) -> None:
        """Starts building the approved spec. The build starts a fresh agent
        conversation: the approved spec is the plan, without the drafting
        history filling the context."""
        if from_ui and session["pr_number"] and not session.get("dry_run"):
            try:
                self.gh.comment_pr(session["repo"], session["pr_number"],
                                   f"{specs.APPROVE_COMMAND}\n\nApproved in Bertha.")
            except Exception:
                logger.exception("failed posting the approval on session %s's PR", session["id"])
        store.update_session(session["id"], spec_phase="building", harness_session_id=None)
        store.set_pending_message(session["id"], specs.build_message(
            self._original_task(session), session["spec_path"], comments or []))
        store.append_log(session["id"], "system", f"Spec approved by {by}; building it")
        self._spec_pr_update(session, "building")

    def abandon_spec(self, session: dict[str, Any], by: str, from_ui: bool = False) -> None:
        """Closes the spec's PR and the session, without building anything."""
        if session["pr_number"] and not session.get("dry_run"):
            try:
                if from_ui:
                    self.gh.comment_pr(session["repo"], session["pr_number"],
                                       f"{specs.ABANDON_COMMAND}\n\nAbandoned in Bertha.")
                self._spec_pr_update(session, "abandoned")
                self.gh.close_pr(session["repo"], session["pr_number"])
            except Exception:
                logger.exception("failed closing session %s's spec PR", session["id"])
        self.runner.remove_volume(store.workspace_of(session))
        store.update_session(session["id"], spec_phase="abandoned", status="closed", pending_message=None,
                             attention=None, harness_session_id=None)
        store.append_log(session["id"], "system", f"Spec abandoned by {by}; the PR is closed")


# How often a streaming ask reply is written to its log entry.
ASK_SAVE_INTERVAL = 0.15


def _stream_deltas(lines: Iterable[str]) -> Iterator[str]:
    """The text pieces of an OpenAI-style streamed chat completion: one
    `data: {json}` server-sent event per chunk, ending with `data: [DONE]`."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            choices = json.loads(payload).get("choices") or []
        except ValueError:
            continue
        content = (choices[0].get("delta") or {}).get("content") if choices else None
        if content:
            yield content


SEVERITIES = ("high", "medium", "low")


def parse_review(message: str) -> dict[str, Any] | None:
    """QC's report: the last JSON object with a findings list in its final
    message, with the findings checked over. Usually a fenced block, but a
    model can end it badly (an unclosed fence, stray tool-call markup), so
    any parseable object counts. None if there isn't a usable one."""
    decoder = json.JSONDecoder()
    candidates = []
    for start in [m.start() for m in re.finditer(r"\{", message)]:
        try:
            data, _end = decoder.raw_decode(message, start)
        except ValueError:
            continue
        if isinstance(data, dict) and "findings" in data:
            candidates.append(data)
    for data in reversed(candidates):
        if not isinstance(data, dict) or not isinstance(data.get("findings", []), list):
            continue
        findings = []
        for f in data.get("findings") or []:
            if not isinstance(f, dict) or not str(f.get("title") or "").strip():
                continue
            severity = str(f.get("severity") or "medium").lower()
            line = f.get("line")
            findings.append({
                "severity": severity if severity in SEVERITIES else "medium",
                "file": str(f.get("file") or "").strip(),
                "line": int(line) if isinstance(line, (int, float)) or str(line).isdigit() else None,
                "title": str(f["title"]).strip()[:200],
                "detail": str(f.get("detail") or "").strip()[:2000],
                "suggestion": str(f.get("suggestion") or "").strip()[:1000],
            })
        findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))
        checks = [str(c) for c in data.get("checks_run") or [] if str(c).strip()]
        return {"summary": str(data.get("summary") or "").strip()[:1000], "checks_run": checks[:20],
                "findings": findings}
    return None


def _where(session: dict[str, Any], finding: dict[str, Any]) -> str:
    if not finding["file"]:
        return ""
    label = f"{finding['file']}:{finding['line']}" if finding["line"] else finding["file"]
    anchor = f"#L{finding['line']}" if finding["line"] else ""
    url = f"https://github.com/{session['repo']}/blob/{session['branch_name']}/{finding['file']}{anchor}"
    return f"[`{label}`]({url})"


def format_findings(session: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    lines = []
    for i, f in enumerate(findings, 1):
        where = _where(session, f)
        lines.append(f"{i}. **{f['severity']}**: {f['title']}" + (f" ({where})" if where else ""))
        if f["detail"]:
            lines.append(f"   {f['detail']}")
        if f["suggestion"]:
            lines.append(f"   *Fix:* {f['suggestion']}")
    return "\n".join(lines)


def review_comment(session: dict[str, Any], report: dict[str, Any]) -> str:
    """QC's review, as posted on the pull request."""
    findings = report["findings"]
    head = ("**QC review: passed.**" if not findings else
            f"**QC review: {len(findings)} finding{'s' if len(findings) != 1 else ''}.** Bertha is fixing "
            f"{'them' if len(findings) != 1 else 'it'} now.")
    parts = [head]
    if report["summary"]:
        parts.append(report["summary"])
    if findings:
        parts.append(format_findings(session, findings))
    if report["checks_run"]:
        parts.append("Checks run: " + ", ".join(f"`{c}`" for c in report["checks_run"]))
    return "\n\n".join(parts)


def _final_message(logs: str) -> str:
    """The agent's last message in a turn's output, which ends with its report."""
    last = ""
    for line in logs.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and (part.get("text") or "").strip():
            last = part["text"]
    return last.strip()[-4000:]
