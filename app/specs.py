"""Spec-first sessions: the agent writes a spec, a person reviews it on a draft
pull request, and only an approved spec gets built, on the same branch and PR.

Phases, stored on the session as `spec_phase`:
    drafting  -> the first turn is writing the spec
    review    -> the spec is on a draft PR, waiting on comments; feedback
                 starts a revise turn, /approve-spec the build, /abandon ends it
    building  -> approved; the build turn implements it
    built     -> built and the PR is ready for review; from here the session
                 is a normal PR session
    abandoned -> closed without building

Spec turns may only write the spec file (agent_image/entrypoint.sh throws
away anything else), so a spec can't quietly turn into an implementation.
"""

import re
from typing import Any

APPROVE_COMMAND = "/approve-spec"
ABANDON_COMMAND = "/abandon"
# On everything Bertha posts, so its own comments never count as feedback.
# The bot often posts as the same account as the reviewer, so the author
# alone can't tell them apart.
MARKER = "<!-- bertha -->"
LABELS = {
    "spec-review": ("fbca04", "Spec awaiting review: comment, or /approve-spec to build"),
    "building": ("0e8a16", "Spec approved; Bertha is building it"),
}
PHASE_LABEL = {"review": "spec-review", "building": "building"}
_STATUS = re.compile(r"<!-- bertha:status -->.*?<!-- /bertha:status -->\n*", re.S)


def spec_path(session: dict[str, Any]) -> str:
    """Where the spec lives in the repo: named after the issue, or the session."""
    slug = re.sub(r"[^a-z0-9]+", "-", (session.get("title") or "").lower()).strip("-")[:48].rstrip("-")
    prefix = str(session["issue_number"]) if session.get("issue_number") else session["ref"]
    return f"specs/{prefix}-{slug or 'spec'}.md"


def status_line(phase: str, path: str, repo: str, branch: str) -> str:
    link = f"[`{path}`](https://github.com/{repo}/blob/{branch}/{path})"
    return {
        "review": f"**Spec awaiting review** ({link}). Comment on the spec with feedback, or comment "
                  f"`{APPROVE_COMMAND}` to build it, or `{ABANDON_COMMAND}` to close this.",
        "revising": f"**Revising the spec** ({link}) from your comments.",
        "building": f"**Spec approved; building it** ({link}). Comments now wait until the build is done.",
        "build_failed": f"**The build didn't finish** ({link}). See the session in Bertha, or comment to retry.",
        "built": f"**Built from the approved spec** ({link}). Ready for review.",
        "abandoned": "**Abandoned** before it was built.",
    }[phase]


def with_status(body: str, line: str) -> str:
    """The PR body with Bertha's status line at the top, replacing any earlier one."""
    block = f"<!-- bertha:status -->\n> {line}\n<!-- /bertha:status -->\n\n"
    return block + _STATUS.sub("", body or "", count=1)


def command_of(body: str) -> str | None:
    """The command a comment gives: a line that is exactly /approve-spec or /abandon."""
    for line in (body or "").splitlines():
        word = line.strip().lower()
        if word in (APPROVE_COMMAND, ABANDON_COMMAND):
            return word
    return None


def describe_comment(c: dict[str, Any]) -> str:
    """One comment as the agent sees it, with where on the spec it was left."""
    where = f" on `{c['path']}` line {c['line']}" if c.get("path") and c.get("line") else (
        f" on `{c['path']}`" if c.get("path") else "")
    return f"@{c['author']}{where}:\n{c['body'].strip()}"


def draft_message(task: str, path: str, branch: str) -> str:
    return f"""You are writing a spec, not code. You are in the git checkout at /data/repo, on branch {branch}. Read the code the task touches, then write the spec to {path}. Change no other file: anything outside the spec is discarded when you finish. A person reviews the spec before any code is written, and the build follows it, so make it specific enough to build from without guessing.

Use these sections, skipping any that don't apply:
- Summary: what changes and why, in two or three sentences.
- Current behaviour: how the code works today, citing files and functions as path:line.
- Approach: the design, and the alternatives you rejected with why.
- Changes: each file or function and what changes in it.
- Data and migrations: schema, stored data, config.
- Risks and edge cases.
- Test plan: the tests you will add or change, and how to check it by hand.
- Out of scope.
- Open questions: decisions the reviewer has to make. Ask instead of guessing.

Keep it as short as the task allows. Finish with a reply of three to six lines summarising the spec and listing the open questions; it becomes the pull request description.

Task:
{task}"""


def revise_message(feedback: str, path: str) -> str:
    return f"""Reviewers commented on the spec at {path}. Update the spec to address each comment, reading the code again where a comment questions it. Change no other file. If you disagree with a comment, keep the spec as it is and say why. Answered open questions move into the spec.

Finish with a reply to the reviewers: for each comment, one or two lines saying what you changed, or why you didn't. It is posted on the pull request.

Comments:

{feedback}"""


def build_message(task: str, path: str, comments: list[dict[str, Any]]) -> str:
    notes = ""
    if comments:
        notes = "\n\nThe reviewer left these comments with the approval; follow them too:\n\n" + \
            "\n\n".join(describe_comment(c) for c in comments)
    return f"""Implement the approved spec at {path}. Read it first: it is the plan, agreed with the reviewer. Follow it, including its test plan. Leave the spec file as approved; if the code forces you to depart from it, do the simplest thing that fits and explain the departure in your final report.

The original task, for context:
{task}{notes}"""
