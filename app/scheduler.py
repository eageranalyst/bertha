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

from . import agent_settings
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
    return "\n".join(parts) if len(parts) > 4 else ""
BUSY_OR_QUEUED = {"running", "queued"}


class Scheduler:
    def __init__(self, settings: Settings, runner: DockerRunner, gh: GitHubClient):
        self.settings = settings
        self.runner = runner
        self.gh = gh
        self._ask_clients: dict[int, httpx.Client] = {}
        # Per code session, the log entry its running turn is writing into and
        # how much of the container's output has been copied there.
        self._streamed: dict[int, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def tick_fast(self) -> None:
        """Everything local -- no GitHub calls, no external rate limit,
        so this runs on a short interval. A freshly created session
        (or one a user just messaged) launches on the next tick of
        *this* loop, not the slow one -- otherwise session creation
        would feel broken, waiting up to a full poll_interval_seconds
        before anything visibly happens.
        """
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
        self._streamed.pop(session["id"], None)
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
        chosen = model_registry.resolve(self.settings, session.get("model"))
        opencode_model = (
            f"{self.settings.local_model_provider}/{chosen['key']}"
            if chosen else self.settings.opencode_model_ref
        )
        env = {
            "REPO": session["repo"] or "",
            "GITHUB_TOKEN": self.settings.github_token,
            "BRANCH_NAME": branch_name,
            "PROMPT": prompt,
            "OPENCODE_SESSION_ID": session["harness_session_id"] or "",
            "OPENCODE_MODEL": opencode_model,
            # The repo's wiki, loaded as standing instructions (like CLAUDE.md).
            "REPO_CONTEXT": repo_context(session["repo"]) if session["repo"] else "",
            # How far this turn may run: steps, continuations, timeout, token budget.
            **agent_settings.env_for_turn(),
            # Every enabled model, so OpenCode knows the full set it can switch to.
            "MODELS_JSON": json.dumps([
                {"key": m["key"], "model_id": m["model_id"]}
                for m in store.list_models() if m["enabled"]
            ]),
            "LOCAL_MODEL_PROVIDER": self.settings.local_model_provider,
            "LOCAL_MODEL_BASE_URL": self.settings.local_model_base_url,
            "LOCAL_MODEL_KEY": self.settings.local_model_key,
            "LOCAL_MODEL_ID": self.settings.local_model_id,
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
        }
        try:
            container_id = self.runner.start_turn(session["id"], store.workspace_of(session), env)
        except Exception:
            logger.exception("failed starting container for session %s", session["id"])
            store.update_session(session["id"], status="failed", attention="failed")
            store.append_log(session["id"], "system", "Failed to start container")
            return
        self._streamed.pop(session["id"], None)
        store.update_session(
            session["id"],
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
        sent = self._streamed.get(session["id"])
        if sent is None:
            log_id, offset = None, 0
        else:
            log_id, offset = sent
        new = logs[offset:]
        cut = new.rfind("\n")
        if cut < 0:
            return
        chunk = new[: cut + 1]
        if log_id is None:
            log_id = store.append_log(session["id"], "agent_output", chunk)
        else:
            store.extend_log(log_id, chunk)
        self._streamed[session["id"]] = (log_id, offset + len(chunk))

    def _finish_code_turn(self, session: dict[str, Any], container: Any) -> None:
        logs = self.runner.logs(session["container_id"])
        # Whatever the stream hasn't copied yet, which is the last partial line
        # and anything after the final tick. Without a stream, all of it.
        sent = self._streamed.pop(session["id"], None)
        if sent is None:
            store.append_log(session["id"], "agent_output", logs)
        else:
            log_id, offset = sent
            rest = logs[offset:]
            if rest:
                store.extend_log(log_id, rest)
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
            try:
                comments = self.gh.new_pr_comments(
                    session["repo"], session["pr_number"], session["last_seen_comment_id"]
                )
            except Exception:
                logger.exception("failed polling PR comments for session %s", session["id"])
                continue
            if not comments:
                continue
            max_id = max(c["id"] for c in comments)
            combined = "\n\n".join(f"@{c['author']}: {c['body']}" for c in comments)
            store.update_session(session["id"], last_seen_comment_id=max_id)
            store.set_pending_message(session["id"], combined)
            store.append_log(session["id"], "pr_comment", combined)
            logger.info("session %s woken by PR comment(s)", session["id"])
