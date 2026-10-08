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
  LOOP=<what>       the agent kept failing on the same thing (see LoopWatch)
  STEP_LIMIT=1      the run used every step it had (AGENT_STEPS): OpenCode
                    then makes the model answer, but the work isn't finished

With --watch, it instead copies the event stream from stdin to stdout and the
run file as it arrives, and stops the agent as soon as it starts looping, so a
loop doesn't use up the run's steps.
"""

import json
import os
import re
import signal
import subprocess
import sys

# The same failure this many times in one run is a loop, not bad luck.
LOOP_REPEATS = 3
# Tool output that means the thing asked for isn't there, though the tool
# itself "completed" (bash's ls and find, the glob tool).
MISSING_RE = re.compile(r"No such file or directory|cannot access|^No files found", re.M)
# The path in a "not found" error, so different tools failing on one
# missing path count as the same failure.
PATH_RE = re.compile(r"(?:File not found|cannot access|No such file or directory)[:\s'\"]*(/[^\s'\":]+)")


def _failure(event: dict) -> str | None:
    """What a tool call failed on, as a key equal for repeats of the same
    failure; None if it didn't fail."""
    if event.get("type") != "tool_use":
        return None
    part = event.get("part") or {}
    state = part.get("state") or {}
    text = str(state.get("error") or "") if state.get("status") == "error" else ""
    if state.get("status") == "completed" and MISSING_RE.search(str(state.get("output") or "")):
        text = str(state.get("output"))
    if not text and state.get("status") != "error":
        return None
    path = PATH_RE.search(text) or PATH_RE.search(json.dumps(state.get("input") or {}))
    if path:
        return f"{path.group(1)} doesn't exist"
    tool_input = state.get("input") or {}
    detail = tool_input.get("command") or tool_input.get("filePath") or tool_input.get("pattern") or json.dumps(tool_input, sort_keys=True)
    return f"{part.get('tool') or 'tool'} {str(detail)[:200]} keeps failing"


class LoopWatch:
    """Counts failures by what they failed on. The first to reach
    LOOP_REPEATS is the loop."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.loop = ""

    def see(self, event: dict) -> bool:
        key = _failure(event)
        if key and not self.loop:
            self.counts[key] = self.counts.get(key, 0) + 1
            if self.counts[key] >= LOOP_REPEATS:
                self.loop = key
                return True
        return False


def summarize(path: str) -> dict[str, str]:
    last_reason = None
    tokens = 0
    # Each step's input is the whole context so far, so summing it counts the
    # same history again every step. What a step adds is the growth in input
    # plus its own output, which is what this measures.
    previous_input = 0
    rejected = ""
    error = ""
    watch = LoopWatch()
    steps = 0
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
        watch.see(event)
        if event.get("type") == "error":
            detail = event.get("error") or {}
            message = (detail.get("data") or {}).get("message") or detail.get("name") or "model error"
            try:
                # LM Studio wraps its message in JSON inside the message.
                message = json.loads(message).get("message", message)
            except (ValueError, AttributeError):
                pass
            error = str(message).replace("\n", " ")[:300]
        elif event.get("type") == "step_start":
            steps += 1
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
        "LOOP": watch.loop.replace("\n", " "),
        "STEP_LIMIT": "1" if 0 < int(os.environ.get("AGENT_STEPS") or 0) <= steps else "",
    }


def watch_stream(path: str) -> None:
    """Tees stdin to stdout and `path`, and stops the agent (every process
    of the node user, which is all the agent runs) when it starts looping.
    The rest of the run is cut off; the summary reports LOOP."""
    watch = LoopWatch()
    with open(path, "a", encoding="utf-8") as out:
        for line in sys.stdin:
            sys.stdout.write(line)
            sys.stdout.flush()
            out.write(line)
            out.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if watch.see(event):
                print(f"=== Stopping the run: {watch.loop} ({LOOP_REPEATS} tries) ===", flush=True)
                subprocess.run(["pkill", "-TERM", "-u", "node"], check=False)
                break
    # Drain whatever the agent writes as it shuts down, so it never blocks.
    for line in sys.stdin:
        pass


if __name__ == "__main__":
    if sys.argv[1] == "--watch":
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        watch_stream(sys.argv[2])
    else:
        for key, value in summarize(sys.argv[1]).items():
            print(f"{key}={value}")
