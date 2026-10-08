"""Thin wrapper over PyGithub for the polling the scheduler needs.

Everything here is a plain REST read except issue/PR fetches, which are
cheap and infrequent (one poll tick per repo, one per sleeping session).
No webhooks: the whole point is this runs with nothing publicly
reachable, so GitHub is only ever polled, never notifies us.
"""

import logging
from typing import Any

import httpx
from github import Github

from .specs import MARKER

logger = logging.getLogger("factory.github")


class GitHubClient:
    def __init__(self, token: str, bot_username: str):
        self._gh = Github(token)
        self._token = token
        self.bot_username = bot_username
        self._writers: dict[tuple[str, str], bool] = {}

    def find_assigned_issues(self, repo_full_name: str) -> list[dict[str, Any]]:
        """Open issues assigned to the bot account, excluding PRs."""
        repo = self._gh.get_repo(repo_full_name)
        issues = repo.get_issues(state="open", assignee=self.bot_username)
        return [
            {"number": i.number, "title": i.title, "body": i.body or "",
             "labels": [label.name for label in i.labels]}
            for i in issues
            if i.pull_request is None
        ]

    def token_info(self) -> dict[str, Any]:
        """What kind of token the bot uses and how much it can do. Classic
        tokens report their scopes; fine-grained ones are limited to the repos
        and permissions chosen when they were made, which is what you want.
        """
        response = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        scopes_header = response.headers.get("X-OAuth-Scopes")
        fine_grained = self._token.startswith("github_pat_")
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()] if scopes_header else []
        broad = {"repo", "admin:org", "delete_repo", "workflow", "admin:repo_hook", "write:packages"}
        return {
            "login": response.json().get("login"),
            "kind": "fine-grained" if fine_grained else "classic",
            "scopes": scopes,
            "broad_scopes": sorted(broad & set(scopes)),
            "expires": response.headers.get("github-authentication-token-expiration"),
        }

    def repo_info(self, repo_full_name: str) -> dict[str, Any]:
        """The repo's own metadata as GitHub reports it, for the Repos tab."""
        repo = self._gh.get_repo(repo_full_name)
        try:
            default_branch_protected: bool | None = repo.get_branch(repo.default_branch).protected
        except Exception:
            default_branch_protected = None
        return {
            "default_branch_protected": default_branch_protected,
            "description": repo.description,
            "default_branch": repo.default_branch,
            "language": repo.language,
            "private": repo.private,
            "archived": repo.archived,
            "html_url": repo.html_url,
            "pushed_at": repo.pushed_at.timestamp() if repo.pushed_at else None,
            "open_issues": repo.open_issues_count,
        }

    def get_issue(self, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        repo = self._gh.get_repo(repo_full_name)
        issue = repo.get_issue(issue_number)
        return {"number": issue.number, "title": issue.title, "body": issue.body or "",
                "pull_request": issue.pull_request is not None}

    def new_pr_comments(
        self, repo_full_name: str, pr_number: int, since_comment_id: int, since: float | None = None,
    ) -> list[dict[str, Any]]:
        """Comments on a PR not seen yet: conversation comments, line comments
        (with the file and line they're on) and review summaries. "Not seen"
        is newer than `since` (epoch seconds) when given, else a comment id
        above since_comment_id. Bertha's own comments are left out by their
        marker, and by the bot's login when the bot has its own account,
        so they can't wake the session they came from. So are GitHub Apps'.
        """
        repo = self._gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        def wanted(item_id: int, at: Any, login: str, body: str | None) -> bool:
            if not (body or "").strip() or MARKER in body or login.endswith("[bot]"):
                return False  # empty, Bertha's own, or an app's (CI previews, say)
            if login == self.bot_username and not self.bot_is_owner(repo_full_name):
                return False
            return at.timestamp() > since if since is not None else item_id > since_comment_id

        comments: list[dict[str, Any]] = []
        for c in pr.get_issue_comments():
            if wanted(c.id, c.created_at, c.user.login, c.body):
                comments.append({"id": c.id, "author": c.user.login, "body": c.body,
                                 "at": c.created_at.timestamp()})
        for c in pr.get_review_comments():
            if wanted(c.id, c.created_at, c.user.login, c.body):
                comments.append({"id": c.id, "author": c.user.login, "body": c.body,
                                 "at": c.created_at.timestamp(), "path": c.path,
                                 "line": c.line or c.original_line})
        if since is not None:  # review ids are a separate sequence, so only by time
            for r in pr.get_reviews():
                if r.submitted_at and wanted(r.id, r.submitted_at, r.user.login, r.body):
                    comments.append({"id": r.id, "author": r.user.login, "body": r.body,
                                     "at": r.submitted_at.timestamp()})
        comments.sort(key=lambda c: c["at"])
        return comments

    def bot_is_owner(self, repo_full_name: str) -> bool:
        """Whether the bot posts as the repo owner's own account, in which case
        its login says nothing about who wrote a comment."""
        return repo_full_name.split("/", 1)[0].lower() == self.bot_username.lower()

    def can_write(self, repo_full_name: str, login: str) -> bool:
        """Whether this person can push to the repo: only they can steer a
        session from a comment."""
        key = (repo_full_name, login)
        if key not in self._writers:
            try:
                permission = self._gh.get_repo(repo_full_name).get_collaborator_permission(login)
            except Exception:
                logger.exception("couldn't check %s's access to %s", login, repo_full_name)
                return False
            self._writers[key] = permission in ("admin", "maintain", "write")
        return self._writers[key]

    def comment_pr(self, repo_full_name: str, pr_number: int, body: str) -> None:
        self._gh.get_repo(repo_full_name).get_issue(pr_number).create_comment(f"{body}\n\n{MARKER}")

    def set_pr_body(self, repo_full_name: str, pr_number: int, edit: Any) -> None:
        """Replaces the PR body with edit(current body)."""
        pr = self._gh.get_repo(repo_full_name).get_pull(pr_number)
        pr.edit(body=edit(pr.body or ""))

    def set_pr_labels(self, repo_full_name: str, pr_number: int, add: str | None, managed: dict[str, tuple[str, str]]) -> None:
        """Puts `add` on the PR and takes off the other labels in `managed`,
        creating the label in the repo the first time it's used."""
        repo = self._gh.get_repo(repo_full_name)
        issue = repo.get_issue(pr_number)
        current = {label.name for label in issue.labels}
        for name in current & set(managed) - {add}:
            issue.remove_from_labels(name)
        if add and add not in current:
            if add not in {label.name for label in repo.get_labels()}:
                color, description = managed[add]
                repo.create_label(add, color, description)
            issue.add_to_labels(add)

    def mark_ready(self, repo_full_name: str, pr_number: int) -> None:
        pr = self._gh.get_repo(repo_full_name).get_pull(pr_number)
        if pr.draft:
            pr.mark_ready_for_review()

    def close_pr(self, repo_full_name: str, pr_number: int) -> None:
        self._gh.get_repo(repo_full_name).get_pull(pr_number).edit(state="closed")

    def file_at(self, repo_full_name: str, path: str, ref: str) -> str | None:
        """A file's text on a branch, or None when it isn't there."""
        try:
            return self._gh.get_repo(repo_full_name).get_contents(path, ref=ref).decoded_content.decode()
        except Exception:
            return None

    def pr_state(self, repo_full_name: str, pr_number: int) -> tuple[str, bool]:
        repo = self._gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        return pr.state, pr.merged
