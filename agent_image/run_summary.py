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
    # Each step's input is the whole context so far, so summing it counts the
    # same history again every step. What a step adds is the growth in input
    # plus its own output, which is what this measures.
    previous_input = 0
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
            usage = part.get("tokens") or {}
            step_input = int(usage.get("input") or 0)
            tokens += max(0, step_input - previous_input) + int(usage.get("output") or 0)
            previous_input = step_input
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
