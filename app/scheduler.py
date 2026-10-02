"""The state machine driving every session.

One asyncio task, one tick every `poll_interval_seconds`. Each tick is
a handful of cheap passes over the sqlite table plus a few GitHub API
calls -- no webhooks, no long-lived connections, nothing that needs to
be reachable from outside this machine.

Session lifecycle:
    queued    -> a turn is about to start (fresh issue, or something
                 woke it and set a pending_message)
    running   -> a container is doing the turn
    pr_open   -> the turn finished, a PR exists, clock is ticking
                 towards sleep
    sleeping  -> idle past the timeout; polled for new PR comments
    failed    -> the turn's container exited non-zero / never opened
                 a PR; stays visible for the UI to retry or close
    closed / archived -> scheduler ignores these entirely
"""

import logging
import time
from typing import Any

from . import store
from .config import Settings
from .docker_runner import DockerRunner
from .github_client import GitHubClient

logger = logging.getLogger("factory.scheduler")

TERMINAL_STATUSES = {"closed", "archived"}


class Scheduler:
    def __init__(self, settings: Settings, runner: DockerRunner, gh: GitHubClient):
        self.settings = settings
        self.runner = runner
        self.gh = gh

    def tick(self) -> None:
        self._discover_new_issues()
        self._promote_pending_messages()
        self._start_queued()
        self._check_running()
        self._check_pr_open_idle()
        self._check_sleeping()

    # -- discovery ---------------------------------------------------

    def _discover_new_issues(self) -> None:
        for repo in self.settings.repos:
            try:
                issues = self.gh.find_assigned_issues(repo)
            except Exception:
                logger.exception("failed listing issues for %s", repo)
                continue
            for issue in issues:
                if store.find_session_by_issue(repo, issue["number"]):
                    continue
                self._create_session(repo, issue)

    def _create_session(self, repo: str, issue: dict[str, Any]) -> int:
        branch = f"factory/issue-{issue['number']}"
        session_id = store.create_session(
            repo=repo,
            issue_number=issue["number"],
            issue_title=issue["title"],
            branch_name=branch,
        )
        store.append_log(
            session_id, "system", f"Picked up issue #{issue['number']}: {issue['title']}"
        )
        store.set_pending_message(session_id, issue["body"] or issue["title"])
        logger.info("session %s created for %s#%s", session_id, repo, issue["number"])
        return session_id

    # -- promotion / launch -------------------------------------------

    def _promote_pending_messages(self) -> None:
        """Any non-terminal, non-busy session with a pending_message is
        ready to run (or re-run): a fresh issue, a UI message, or a new
        PR comment picked up while sleeping all land here the same way.
        """
        for session in store.list_sessions():
            if session["status"] in TERMINAL_STATUSES | {"running", "queued"}:
                continue
            if session["pending_message"]:
                store.update_session(session["id"], status="queued")

    def _start_queued(self) -> None:
        for session in store.list_sessions(status="queued"):
            self._launch_turn(session)

    def _launch_turn(self, session: dict[str, Any]) -> None:
        prompt = session["pending_message"] or session["issue_title"]
        is_resume = bool(session["harness_session_id"])
        env = {
            "REPO": session["repo"],
            "GITHUB_TOKEN": self.settings.github_token,
            "BRANCH_NAME": session["branch_name"],
            "PROMPT": prompt,
            "OPENCODE_SESSION_ID": session["harness_session_id"] or "",
            "OPENCODE_MODEL": self.settings.opencode_model_ref,
            "LOCAL_MODEL_PROVIDER": self.settings.local_model_provider,
            "LOCAL_MODEL_BASE_URL": self.settings.local_model_base_url,
            "LOCAL_MODEL_KEY": self.settings.local_model_key,
            "LOCAL_MODEL_ID": self.settings.local_model_id,
            "BOT_NAME": self.settings.bot_name,
            "BOT_EMAIL": self.settings.bot_email,
            "PR_NUMBER": str(session["pr_number"] or ""),
            "PR_TITLE": f"{session['issue_title']} (closes #{session['issue_number']})",
            "PR_BODY": (
                f"Automated changes for #{session['issue_number']}.\n\n"
                f"Closes #{session['issue_number']}"
            ),
        }
        try:
            container_id = self.runner.start_turn(session["id"], env)
        except Exception:
            logger.exception("failed starting container for session %s", session["id"])
            store.update_session(session["id"], status="failed")
            store.append_log(session["id"], "system", "Failed to start container")
            return
        store.update_session(
            session["id"],
            status="running",
            container_id=container_id,
            pending_message=None,
            turn_count=session["turn_count"] + 1,
        )
        store.append_log(session["id"], "user_message" if is_resume else "system", prompt)

    # -- running --------------------------------------------------------

    def _check_running(self) -> None:
        for session in store.list_sessions(status="running"):
            if not session["container_id"]:
                continue
            try:
                container = self.runner.poll(session["container_id"])
            except Exception:
                logger.exception("lost track of container for session %s", session["id"])
                continue
            if container.status != "exited":
                continue
            self._finish_turn(session, container)

    def _finish_turn(self, session: dict[str, Any], container: Any) -> None:
        logs = self.runner.logs(session["container_id"])
        store.append_log(session["id"], "agent_output", logs)
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
        new_status = "pr_open" if (exit_code == 0 and pr_number and not agent_failed) else "failed"
        store.update_session(
            session["id"],
            status=new_status,
            container_id=None,
            pr_number=pr_number,
            harness_session_id=harness_session_id,
            last_activity_at=time.time(),
        )
        logger.info(
            "session %s turn finished: exit=%s pr=%s -> %s",
            session["id"], exit_code, pr_number, new_status,
        )

    # -- idle / sleep ----------------------------------------------------

    def _check_pr_open_idle(self) -> None:
        cutoff = time.time() - self.settings.sleep_after_minutes * 60
        for session in store.list_sessions(status="pr_open"):
            if session["last_activity_at"] <= cutoff:
                store.update_session(session["id"], status="sleeping")
                store.append_log(session["id"], "system", "Sleeping after idle timeout")

    def _check_sleeping(self) -> None:
        for session in store.list_sessions(status="sleeping"):
            if not session["pr_number"]:
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
