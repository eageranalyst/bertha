"""Thin wrapper over PyGithub for the polling the scheduler needs.

Everything here is a plain REST read except issue/PR fetches, which are
cheap and infrequent (one poll tick per repo, one per sleeping session).
No webhooks: the whole point is this runs with nothing publicly
reachable, so GitHub is only ever polled, never notifies us.
"""

import logging
from typing import Any

from github import Github

logger = logging.getLogger("factory.github")


class GitHubClient:
    def __init__(self, token: str, bot_username: str):
        self._gh = Github(token)
        self.bot_username = bot_username

    def find_assigned_issues(self, repo_full_name: str) -> list[dict[str, Any]]:
        """Open issues assigned to the bot account, excluding PRs."""
        repo = self._gh.get_repo(repo_full_name)
        issues = repo.get_issues(state="open", assignee=self.bot_username)
        return [
            {"number": i.number, "title": i.title, "body": i.body or ""}
            for i in issues
            if i.pull_request is None
        ]

    def repo_info(self, repo_full_name: str) -> dict[str, Any]:
        """The repo's own metadata as GitHub reports it, for the Repos tab."""
        repo = self._gh.get_repo(repo_full_name)
        return {
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
        return {"number": issue.number, "title": issue.title, "body": issue.body or ""}

    def new_pr_comments(
        self, repo_full_name: str, pr_number: int, since_comment_id: int
    ) -> list[dict[str, Any]]:
        """Issue-style and review comments on a PR newer than since_comment_id,
        excluding ones the bot itself posted (avoids self-triggered wake loops).
        """
        repo = self._gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        comments: list[dict[str, Any]] = []
        for c in pr.get_issue_comments():
            if c.id > since_comment_id and c.user.login != self.bot_username:
                comments.append({"id": c.id, "author": c.user.login, "body": c.body})
        for c in pr.get_review_comments():
            if c.id > since_comment_id and c.user.login != self.bot_username:
                comments.append({"id": c.id, "author": c.user.login, "body": c.body})
        comments.sort(key=lambda c: c["id"])
        return comments

    def pr_state(self, repo_full_name: str, pr_number: int) -> tuple[str, bool]:
        repo = self._gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        return pr.state, pr.merged
