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
    # The environment variable the entrypoint reads it from; None for settings
    # Bertha uses itself.
    env: str | None


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
    Setting(
        key="min_code_context",
        label="Minimum context for code sessions",
        help="Routing won't send a code turn to a model with a smaller context window. A code turn's prompt alone is around 15k tokens.",
        default=32_768,
        minimum=4_096,
        maximum=1_048_576,
        env=None,
    ),
    Setting(
        key="working_context",
        label="Working context (tokens)",
        help="The agent compacts its conversation when it reaches this size, rather than at the model's full context. Smaller keeps each step fast; too small loses detail. Capped at the model's loaded context.",
        # 64k: measured on an M5 Pro with Qwen 35B-A3B, cached steps stay under
        # 2s and a full reprocess after compaction takes about a minute.
        default=65_536,
        minimum=16_384,
        maximum=1_048_576,
        env=None,
    ),
    Setting(
        key="github_max_edits",
        label="GitHub edits per turn",
        help="Issue and pull request edits and comments one turn may make. Further changes are refused.",
        default=30,
        minimum=0,
        maximum=500,
        env=None,
    ),
    Setting(
        key="github_max_closes",
        label="GitHub closures per turn",
        help="For sessions with full GitHub tools (e.g. backlog grooming): issues one turn may close (as done, stale or duplicate).",
        default=5,
        minimum=0,
        maximum=100,
        env=None,
    ),
    Setting(
        key="github_max_pushes",
        label="GitHub pushes per turn",
        help="Times one turn may push its branch mid-turn (to run CI, say). Bertha's push at the end of the turn doesn't count.",
        default=10,
        minimum=0,
        maximum=100,
        env=None,
    ),
    Setting(
        key="container_memory_gb",
        label="Container memory (GB)",
        help="Memory limit for each agent and wiki build container. A container that exceeds it is stopped.",
        default=4,
        minimum=1,
        maximum=64,
        env=None,
    ),
    Setting(
        key="container_cpus",
        label="Container CPUs",
        help="CPU cores each agent and wiki build container may use.",
        default=2,
        minimum=1,
        maximum=32,
        env=None,
    ),
)

# Processes per container: enough for builds and test runners, but stops a fork bomb.
CONTAINER_PIDS_LIMIT = 1024


def container_limits() -> dict[str, int]:
    """docker-py keyword arguments limiting one container's resources."""
    values = current()
    return {
        "mem_limit": values["container_memory_gb"] * 1024**3,
        "nano_cpus": values["container_cpus"] * 1_000_000_000,
        "pids_limit": CONTAINER_PIDS_LIMIT,
    }
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
        if s.env is None:
            continue
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
