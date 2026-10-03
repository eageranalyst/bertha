"""Checks run before the server starts, so a misconfigured or half-started
environment fails with a plain message instead of a stack trace.

Raising SystemExit with a string prints just that string and exits with
status 1. Uvicorn logs a full traceback when the app itself fails during
startup, which is why these checks run at import time, before uvicorn
hands the app over.
"""

import os

REQUIRED_ENV = {
    "GITHUB_TOKEN": "a GitHub personal access token for the bot account (see .env.example)",
    "GITHUB_BOT_USERNAME": "the GitHub username the agent acts as",
}


def _missing_env() -> list[str]:
    problems = []
    for name, purpose in REQUIRED_ENV.items():
        if not os.environ.get(name):
            problems.append(f"{name} is not set. It should be {purpose}.")
    return problems


def _docker_problem() -> list[str]:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return [
            "Docker isn't reachable. Start Docker Desktop, wait for it to finish "
            "starting, then start this server again."
        ]
    return []


def run_preflight() -> None:
    problems = _missing_env()
    # Only probe Docker if the basics are in place; otherwise
    # the message list would bury the actual fix.
    if not problems:
        problems += _docker_problem()
    if not problems:
        return
    lines = ["", "Agent Factory can't start yet:", ""]
    lines += [f"  - {p}" for p in problems]
    lines += [""]
    raise SystemExit("\n".join(lines))
