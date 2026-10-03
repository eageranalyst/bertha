"""Reads one OpenCode run's JSON event stream and reports how it ended.

Printed as key=value lines for the entrypoint:

  STATE=done        the last step finished with a stop: the agent answered
  STATE=unfinished  the run ended mid-work, e.g. after a rejected tool call
  TOKENS=<n>        tokens used by this run
  REJECTED=<cmd>    the command of the last failed or rejected tool call, if any
"""

import json
import sys


def summarize(path: str) -> dict[str, str]:
    last_reason = None
    tokens = 0
    rejected = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "step_finish":
            last_reason = part.get("reason")
            tokens += int((part.get("tokens") or {}).get("total") or 0)
        elif event.get("type") == "tool_use":
            state = part.get("state") or {}
            if state.get("status") == "error":
                tool_input = state.get("input") or {}
                rejected = str(tool_input.get("command") or part.get("tool") or "")
    return {
        "STATE": "done" if last_reason == "stop" else "unfinished",
        "TOKENS": str(tokens),
        "REJECTED": rejected,
    }


if __name__ == "__main__":
    for key, value in summarize(sys.argv[1]).items():
        print(f"{key}={value}")
