"""Reads one OpenCode run's JSON event stream and reports how it ended.

Printed as key=value lines for the entrypoint:

  STATE=done        the last step finished with a stop: the agent answered
  STATE=unfinished  the run ended mid-work, e.g. after a rejected tool call
  TOKENS=<n>        tokens used by this run
  REJECTED=<cmd>    the command of the last failed or rejected tool call, if any
  STATE=error       the model itself returned an error; resuming won't help
  ERROR=<text>      that error's message
  CONTEXT=1         the error was the prompt not fitting the model's context
  UNLOADABLE=1      the server couldn't load the model (e.g. not enough memory)
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
    error = ""
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
        if event.get("type") == "error":
            detail = event.get("error") or {}
            message = (detail.get("data") or {}).get("message") or detail.get("name") or "model error"
            try:
                # LM Studio wraps its message in JSON inside the message.
                message = json.loads(message).get("message", message)
            except (ValueError, AttributeError):
                pass
            error = str(message).replace("\n", " ")[:300]
        elif event.get("type") == "step_finish":
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
    if last_reason == "stop":
        state = "done"
    elif error:
        state = "error"
    else:
        state = "unfinished"
    return {
        "STATE": state,
        "TOKENS": str(tokens),
        "REJECTED": rejected,
        "ERROR": error,
        "CONTEXT": "1" if "context length" in error.lower() else "",
        "UNLOADABLE": "1" if "failed to load model" in error.lower() else "",
    }


if __name__ == "__main__":
    for key, value in summarize(sys.argv[1]).items():
        print(f"{key}={value}")
