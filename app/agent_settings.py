"""Limits on how far a code turn may run, set in the Settings screen.

These bound a turn so it can't loop or burn tokens without end. The agent
gets them as environment variables when a turn starts, so a change applies to
the next turn. Values are stored as text and checked against the ranges here.
"""

from dataclasses import dataclass
from typing import Any

from . import store


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str
    default: int
    minimum: int
    maximum: int
    # The environment variable the entrypoint reads it from.
    env: str


SETTINGS: tuple[Setting, ...] = (
    Setting(
        key="steps",
        label="Steps per run",
        help="Agent iterations (tool calls and replies) in one run. When reached, the agent must answer without more tools.",
        default=25,
        minimum=1,
        maximum=200,
        env="AGENT_STEPS",
    ),
    Setting(
        key="continuations",
        label="Continuations",
        help="How many times to resume an unfinished run in the same session. 0 means one run only.",
        default=2,
        minimum=0,
        maximum=5,
        env="AGENT_CONTINUATIONS",
    ),
    Setting(
        key="run_timeout_minutes",
        label="Run timeout (minutes)",
        help="Wall-clock limit for one run. A run that exceeds it is stopped.",
        default=15,
        minimum=1,
        maximum=120,
        env="AGENT_RUN_TIMEOUT_SECONDS",
    ),
    Setting(
        key="token_budget",
        label="Token budget per turn",
        help="Once a turn has used this many tokens across its runs, no more continuations are started.",
        default=150_000,
        minimum=10_000,
        maximum=2_000_000,
        env="AGENT_TOKEN_BUDGET",
    ),
)
BY_KEY = {s.key: s for s in SETTINGS}


def current() -> dict[str, int]:
    """Every setting's effective value: the stored one, or the default."""
    stored = store.get_settings_values()
    values = {}
    for s in SETTINGS:
        try:
            value = int(stored[s.key]) if s.key in stored else s.default
        except ValueError:
            value = s.default
        values[s.key] = min(max(value, s.minimum), s.maximum)
    return values


def env_for_turn() -> dict[str, str]:
    """The settings as environment variables for a code turn."""
    values = current()
    env = {}
    for s in SETTINGS:
        value = values[s.key]
        env[s.env] = str(value * 60) if s.key == "run_timeout_minutes" else str(value)
    return env


def describe() -> list[dict[str, Any]]:
    values = current()
    return [
        {"key": s.key, "label": s.label, "help": s.help, "value": values[s.key],
         "default": s.default, "minimum": s.minimum, "maximum": s.maximum}
        for s in SETTINGS
    ]


def validate_update(updates: dict[str, Any]) -> dict[str, str]:
    """Checks a partial update and returns it as stored text. Raises ValueError
    with a message for the first problem.
    """
    clean: dict[str, str] = {}
    for key, raw in updates.items():
        if key not in BY_KEY:
            raise ValueError(f"unknown setting {key}")
        s = BY_KEY[key]
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{s.label} must be a whole number")
        if not s.minimum <= value <= s.maximum:
            raise ValueError(f"{s.label} must be between {s.minimum:,} and {s.maximum:,}")
        clean[key] = str(value)
    return clean
