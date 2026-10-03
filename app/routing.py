"""Per-turn model routing.

Before a turn runs, the lightest enabled model reads the task and says which
tier it needs: light, standard or strong. The turn then runs on an enabled
model in that tier. Routing never touches a session whose model was pinned by
the user, and it only changes the model when the tier actually changes, so a
session doesn't keep reloading models for no reason.

A turn that failed moves the next one up a tier. If the classifier can't be
reached or can't answer, the turn keeps the session's current model.
"""

import json
import logging
import re
from typing import Any

import httpx

from . import models as registry
from . import store
from .config import Settings

logger = logging.getLogger("factory.routing")

TIERS = ("light", "standard", "strong")
CLASSIFY_TIMEOUT_SECONDS = 20
MAX_TASK_CHARS = 1500
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = """You choose which model should handle a task, to keep work cheap without sacrificing quality.

Tiers:
- light: quick questions, lookups, explanations, summaries, a small edit to one file.
- standard: everyday coding and writing, a few related edits, a clear bug fix.
- strong: hard debugging, design decisions, large or ambiguous changes across many files.

Reply with only a JSON object: {"tier": "light" | "standard" | "strong", "reason": "<one short sentence>"}"""


def escalate(tier: str) -> str:
    return TIERS[min(TIERS.index(tier) + 1, len(TIERS) - 1)]


def pick_model(enabled: list[dict[str, Any]], current: dict[str, Any] | None, tier: str) -> dict[str, Any] | None:
    """An enabled model in `tier`, keeping `current` when it already is one.
    Otherwise the nearest tier with a model, preferring the stronger of two
    equally near tiers.
    """
    if current and current["tier"] == tier:
        return current
    target = TIERS.index(tier)
    for candidate in sorted(TIERS, key=lambda t: (abs(TIERS.index(t) - target), -TIERS.index(t))):
        matches = sorted((m for m in enabled if m["tier"] == candidate), key=lambda m: m["key"])
        if matches:
            return matches[0]
    return None


def classify(settings: Settings, light: dict[str, Any], task: dict[str, Any]) -> tuple[str, str] | None:
    """(tier, reason) from the light model, or None if it didn't give a usable answer."""
    user = "\n".join([
        f"Session type: {task['session_type']}",
        f"Repository attached: {'yes' if task['has_repo'] else 'no'}",
        f"The previous turn failed: {'yes' if task['previous_failed'] else 'no'}",
        "",
        "Task:",
        task["prompt"][:MAX_TASK_CHARS],
    ])
    try:
        response = httpx.post(
            f"{settings.local_model_host_base_url}/chat/completions",
            json={
                "model": light["model_id"],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "temperature": 0,
                "max_tokens": 200,
            },
            timeout=CLASSIFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("routing classifier unavailable, keeping the current model: %s", exc)
        return None

    content = THINK_RE.sub("", content or "")
    match = JSON_RE.search(content)
    if not match:
        return None
    try:
        answer = json.loads(match.group(0))
    except ValueError:
        return None
    tier = answer.get("tier")
    if tier not in TIERS:
        return None
    reason = str(answer.get("reason") or "").strip()[:160]
    return tier, reason or "no reason given"


def route(settings: Settings, session: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The model this turn should run on, and why it changed (None when it didn't).

    `task` has session_type, has_repo and prompt. The session's own previous
    failure is read from the session.
    """
    current = registry.resolve(settings, session.get("model"))
    if not settings.routing_enabled or session.get("model_pinned"):
        return current, None

    enabled = [m for m in store.list_models() if m["enabled"]]
    light = pick_model(enabled, None, "light")
    if light is None or not task["prompt"].strip():
        return current, None

    answer = classify(settings, light, {**task, "previous_failed": bool(session.get("last_turn_failed"))})
    if answer is None:
        return current, None
    tier, reason = answer
    if session.get("last_turn_failed"):
        tier = escalate(tier)
        reason = f"{reason} (the last turn failed, so this one goes up a tier)"
    chosen = pick_model(enabled, current, tier)
    return chosen or current, reason
