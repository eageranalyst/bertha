"""Checks run before the server starts, so a misconfigured or half-started
environment fails with a plain message instead of a stack trace.

Raising SystemExit with a string prints just that string and exits with
status 1. Uvicorn logs a full traceback when the app itself fails during
startup, which is why these checks run at import time, before uvicorn
hands the app over.
"""

import os
import socket

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


def _port_problem(host: str, port: int) -> list[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Match uvicorn, which sets this too. Without it, connections left
        # in TIME_WAIT after a server stops make the port look busy.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return [
                f"Port {port} is already in use. Another copy of the server is probably "
                f"running (a terminal session, or a launchd agent). Stop that one, or set "
                f"FACTORY_PORT to a different port."
            ]
    return []


def run_preflight() -> None:
    host = os.environ.get("FACTORY_HOST", "127.0.0.1")
    port = int(os.environ.get("FACTORY_PORT", "8787"))
    problems = _missing_env()
    # Only probe Docker and the port if the basics are in place; otherwise
    # the message list would bury the actual fix.
    if not problems:
        problems += _docker_problem()
        problems += _port_problem(host, port)
    if not problems:
        return
    lines = ["", "Agent Factory can't start yet:", ""]
    lines += [f"  - {p}" for p in problems]
    lines += [""]
    raise SystemExit("\n".join(lines))
