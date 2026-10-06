"""Runs Bertha's agent on a fixed set of tasks and checks the results.

Each task in tasks.json runs as a dry-run code session through Bertha's API:
the agent works as normal, and commits in its workspace, but never pushes or
opens a PR. When the session finishes, its checks run against the workspace
and the agent's reply. Results go to evals/results/<time>.jsonl, so runs
before and after a change can be compared.

    python evals/run.py                       # every task, default routing
    python evals/run.py --model qwen3.6-35b-a3b --task map-pin-image

Check types (all must pass):
    {"changed": "path"}            the path was modified (glob allowed)
    {"diff_contains": "regex"}     an added line matches
    {"reply_contains": "regex"}    the agent's final message matches (case-insensitive)
    {"command": "npm test"}        exits 0 in the workspace (cwd /data/repo)
    {"no_changes": true}           nothing was changed

Needs the API running (BERTHA_API, default http://127.0.0.1:8787) and Docker.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import docker
import httpx

HERE = Path(__file__).resolve().parent
API = os.environ.get("BERTHA_API", "http://127.0.0.1:8787")
AGENT_IMAGE = os.environ.get("FACTORY_AGENT_IMAGE_TAG", "factory-agent:latest")
DONE = {"idle", "pr_open", "failed", "stopped", "closed", "sleeping"}


def run_task(client: httpx.Client, task: dict, model: str | None, timeout: int) -> dict:
    started = time.time()
    session = client.post("/api/sessions", json={
        "session_type": "code", "repo": task["repo"], "prompt": task["prompt"],
        "model": model, "dry_run": True,
    }).raise_for_status().json()
    sid = session["id"]
    print(f"  session {session['ref']} started", flush=True)
    while True:
        time.sleep(5)
        session = client.get(f"/api/sessions/{sid}").raise_for_status().json()
        if session["status"] in DONE and not session["pending_message"]:
            break
        if time.time() - started > timeout:
            client.post(f"/api/sessions/{sid}/stop")
            session["status"] = "timed out"
            break
    logs = client.get(f"/api/sessions/{sid}/logs").raise_for_status().json()
    output = "\n".join(l["content"] for l in logs if l["kind"] == "agent_output")
    result = {
        "task": task["id"], "session": session["ref"], "status": session["status"],
        "model": session["model"], "seconds": round(time.time() - started),
        "runs": output.count("=== Run finished"),
        "tokens": sum(int(m) for m in re.findall(r"turn total (\d+)\)", output)[-1:]),
        "routed": [l["content"] for l in logs if l["content"].startswith(("Routed to", "Retrying on"))],
        "reply": final_reply(output),
        "workspace": session.get("workspace") or f"factory-ws-{sid}",
        "session_id": sid,
    }
    return result


def final_reply(output: str) -> str:
    reply = ""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        text = (event.get("part") or {}).get("text") or ""
        if event.get("type") == "text" and text.strip():
            reply = text.strip()
    return reply


def in_workspace(docker_client, volume: str, script: str) -> tuple[int, str]:
    container = docker_client.containers.run(
        AGENT_IMAGE, entrypoint=["bash", "-c", script], volumes={volume: {"bind": "/data", "mode": "rw"}},
        working_dir="/data/repo", detach=True,
    )
    try:
        code = container.wait(timeout=1800)["StatusCode"]
        return code, container.logs().decode(errors="replace")
    finally:
        container.remove(force=True)


def check(docker_client, result: dict, checks: list[dict]) -> list[dict]:
    git = "git -c safe.directory='*'"
    _, changed = in_workspace(docker_client, result["workspace"],
                              f"{git} diff --name-only origin/HEAD 2>/dev/null; {git} ls-files --others --exclude-standard")
    changed_files = sorted({f for f in changed.splitlines() if f.strip()})
    _, diff = in_workspace(docker_client, result["workspace"], f"{git} diff origin/HEAD 2>/dev/null")
    added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    result["changed_files"] = changed_files

    outcomes = []
    for c in checks:
        if "changed" in c:
            ok = any(fnmatch.fnmatch(f, c["changed"]) for f in changed_files)
        elif "diff_contains" in c:
            ok = re.search(c["diff_contains"], added) is not None
        elif "reply_contains" in c:
            ok = re.search(c["reply_contains"], result["reply"], re.IGNORECASE) is not None
        elif "command" in c:
            code, out = in_workspace(docker_client, result["workspace"], c["command"])
            ok = code == 0
            c = {**c, "output_tail": out[-800:]}
        elif c.get("no_changes"):
            ok = not changed_files
        else:
            ok = False
        outcomes.append({**c, "passed": ok})
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default=str(HERE / "tasks.json"))
    parser.add_argument("--task", action="append", help="run only these task ids")
    parser.add_argument("--model", help="pin every session to this model (default: routing)")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds per task")
    parser.add_argument("--keep", action="store_true", help="keep the sessions instead of archiving them")
    args = parser.parse_args()

    tasks = json.loads(Path(args.tasks).read_text())
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    docker_client = docker.from_env()
    results = []

    with httpx.Client(base_url=API, timeout=60) as client:
        for task in tasks:
            print(f"{task['id']}: {task['prompt'][:70]}", flush=True)
            result = run_task(client, task, args.model, args.timeout)
            result["checks"] = check(docker_client, result, task["checks"])
            result["passed"] = all(c["passed"] for c in result["checks"])
            if not args.keep:
                client.post(f"/api/sessions/{result['session_id']}/archive")
            with out_path.open("a") as fh:
                fh.write(json.dumps(result) + "\n")
            results.append(result)
            print(f"  {'PASS' if result['passed'] else 'FAIL'} in {result['seconds']}s, {result['runs']} run(s), "
                  f"{result['tokens']} tokens, model {result['model']}, status {result['status']}", flush=True)

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed; "
          f"{sum(r['seconds'] for r in results)}s, {sum(r['tokens'] for r in results)} tokens in total")
    print(f"results: {out_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
