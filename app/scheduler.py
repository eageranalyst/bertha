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

import json
import logging
import threading
import time
from typing import Any

import httpx

from datetime import datetime, timezone

from . import agent_settings, code_index, cron, specs
from . import models as model_registry
from . import store
from .routing import route as route_turn
from .config import Settings
from .docker_runner import DockerRunner
from .github_client import GitHubClient

logger = logging.getLogger("factory.scheduler")

TERMINAL_STATUSES = {"closed", "archived"}

# Wiki pages a coding agent gets as standing context, most useful first, and
# the most text that adds to every turn's prompt.
CONTEXT_PAGES = ("conventions", "overview", "architecture", "testing", "getting-started")
CONTEXT_MAX_CHARS = 12_000


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


def working_context(model: dict[str, Any]) -> int:
    """The context size OpenCode treats as full for this model, so it compacts
    there: the Working context setting, capped at what the model has loaded.
    """
    target = agent_settings.current()["working_context"]
    return min(target, model["context_length"]) if model.get("context_length") else target


def repo_context(repo: str) -> str:
    """The repo's wiki pages as one markdown document, for the agent. Each
    page's Sources list is dropped; it's links for people, not context.
    Empty when the repo has no wiki yet.
    """
    parts = ["# Repository context", "",
             f"Bertha's wiki for {repo}. It was written from the code, so check it "
             "against the code when it matters.", ""]
    used = sum(len(p) for p in parts)
    for slug in CONTEXT_PAGES:
        page = store.get_wiki_page(repo, slug)
        if not page:
            continue
        body = page["body"].split("\n## Sources", 1)[0].strip()
        section = f"## {page['title']}\n\n{body}\n"
        if used + len(section) > CONTEXT_MAX_CHARS:
            section = section[: max(0, CONTEXT_MAX_CHARS - used)].rstrip() + "\n… (truncated)\n"
        parts.append(section)
        used += len(section)
        if used >= CONTEXT_MAX_CHARS:
            break
    code_map = store.get_code_map(repo)
    if code_map and code_map["map"]:
        parts.append(
            "## Repository map\n\nEach source file with its top-level definitions, "
            f"as of commit {code_map['commit_sha'][:7]}.\n\n```\n{code_map['map']}\n```\n"
        )
    return "\n".join(parts) if len(parts) > 4 else ""
BUSY_OR_QUEUED = {"running", "queued"}


class Scheduler:
    def __init__(self, settings: Settings, runner: DockerRunner, gh: GitHubClient):
        self.settings = settings
        self.runner = runner
        self.gh = gh
        self._ask_clients: dict[int, httpx.Client] = {}
        self._lock = threading.Lock()

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
        self._check_sleeping()

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
        """A fresh key for this turn's GitHub tools, when the session has them.
        The previous turn's key is revoked first; this one is revoked when the
        turn ends (see _settle and stop_turn)."""
        if not session.get("github_tools") or not session.get("repo"):
            return {}
        store.revoke_grants(session["id"])
        limits = agent_settings.current()
        key = store.create_grant(session["id"], session["repo"],
                                 limits["github_max_edits"], limits["github_max_closes"])
        host = "egress" if self.settings.egress_isolation else "host.docker.internal"
        return {"GITHUB_TOOLS_URL": f"http://{host}:{self.settings.tools_port}/mcp", "GITHUB_TOOLS_KEY": key}

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
        if not messages:
            # Nothing to answer -- the model server rejects an empty
            # message list, so don't call it.
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
        messages = []
        for log in store.list_logs(session_id):
            if log["kind"] == "user_message":
                messages.append({"role": "user", "content": log["content"]})
            elif log["kind"] == "ask_response":
                messages.append({"role": "assistant", "content": log["content"]})
        return messages

    def _run_ask_request(
        self, session_id: int, client: httpx.Client, messages: list[dict[str, str]], model_id: str
    ) -> None:
        try:
            resp = client.post(
                f"{self.settings.local_model_host_base_url}/chat/completions",
                json={
                    "model": model_id,
                    "messages": messages,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            store.append_log(session_id, "ask_response", text)
            self._settle(session_id, "idle", "replied", last_activity_at=time.time())
        except Exception as exc:
            # Either a real failure, or stop_turn() closed the client out
            # from under us -- can't cleanly tell which, so the log line
            # carries the detail and the status just reflects "not running
            # anymore" rather than guessing which one happened.
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
            # The repo's wiki, loaded as standing instructions (like CLAUDE.md).
            "REPO_CONTEXT": repo_context(session["repo"]) if session["repo"] else "",
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
        if "FACTORY_RUN_TIMED_OUT=1" in logs:
            store.append_log(session["id"], "system", "The agent run went past its time limit and was stopped")
            if session.get("schedule_id"):
                # Recorded before _settle, which would otherwise record a plain failure.
                store.finish_schedule_run(session["id"], "timed_out", "the agent run went past the time left")
        if exit_code != 0 or agent_failed:
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
        self._settle(
            session["id"],
            new_status,
            attention,
            container_id=None,
            pr_number=pr_number,
            harness_session_id=harness_session_id,
            last_activity_at=time.time(),
        )
        logger.info(
            "session %s turn finished: exit=%s pr=%s -> %s",
            session["id"], exit_code, pr_number, new_status,
        )
        if session.get("spec_phase") and session["repo"]:
            try:
                self._after_spec_turn(store.get_session(session["id"]), logs, new_status)
            except Exception:
                logger.exception("failed updating the spec PR for session %s", session["id"])
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

    def _retry_on_another_model(self, session: dict[str, Any], problem: str, advice: str) -> None:
        """A turn whose model couldn't take the request never started, so it's
        run again on another model, excluding the one that failed. A pinned
        session isn't moved; the failure says why instead.
        """
        failed_model = session["model"]
        if session["model_pinned"]:
            store.append_log(session["id"], "system", f"{failed_model} can't run this turn: {problem}. {advice}")
            return
        prompt = next(
            (l["content"] for l in reversed(store.list_logs(session["id"]))
             if l["kind"] in ("user_message", "pr_comment")),
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
            store.update_session(session["id"], spec_phase="built")
            if session["pr_number"] and not session.get("dry_run"):
                self.gh.mark_ready(session["repo"], session["pr_number"])
            self._spec_pr_update(session, "built")

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
                 "building": "building", "build_failed": "building"}.get(state)
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
