"""The local model registry: discovery, tiers, and choosing a model.

The model server (LM Studio, or anything OpenAI-compatible) reports what it
has loaded or can load. Those are registered here with a tier inferred from
their size, and the tier and description can be edited afterwards. A session
records the model it was created with; a turn resolves that to a registered,
enabled model, and falls back to the default when there isn't one.
"""

import logging
import re
from typing import Any

import httpx

from . import store
from .config import Settings

logger = logging.getLogger("factory.models")

TIERS = ("light", "standard", "strong")
EMBEDDING_RE = re.compile(r"embed", re.IGNORECASE)
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b(?![a-z])", re.IGNORECASE)


def key_for(model_id: str) -> str:
    """The short name a model is registered under: the id without its publisher."""
    return model_id.split("/")[-1]


def infer_tier(model_id: str) -> str:
    """A starting tier from the parameter count in the name ("qwen3-27b" is
    strong, "llama-3.2-3b" is light). Unknown sizes are standard. Editable.
    """
    match = SIZE_RE.search(key_for(model_id))
    if not match:
        return "standard"
    size = float(match.group(1))
    if size <= 8:
        return "light"
    if size <= 14:
        return "standard"
    return "strong"


def discover(settings: Settings) -> list[str]:
    """Model ids the server reports, minus embedding models, which can't run a
    session. Raises if the server can't be reached.
    """
    response = httpx.get(f"{settings.local_model_host_base_url}/models", timeout=10)
    response.raise_for_status()
    return [
        m["id"] for m in response.json().get("data", [])
        if not EMBEDDING_RE.search(m["id"])
    ]


def refresh(settings: Settings) -> list[dict[str, Any]]:
    for model_id in discover(settings):
        store.add_discovered_model(key_for(model_id), model_id, infer_tier(model_id))
    return store.list_models()


def default_model(settings: Settings) -> dict[str, Any] | None:
    """FACTORY_DEFAULT_MODEL if it names an enabled model; otherwise the
    strongest enabled model.
    """
    enabled = [m for m in store.list_models() if m["enabled"]]
    if settings.default_model_key:
        pinned = next((m for m in enabled if m["key"] == settings.default_model_key), None)
        if pinned:
            return pinned
    for tier in reversed(TIERS):
        match = next((m for m in enabled if m["tier"] == tier), None)
        if match:
            return match
    return None


def resolve(settings: Settings, session_model: str | None) -> dict[str, Any] | None:
    """The model a turn should run on: the session's own, if it's still
    registered and enabled, otherwise the default.
    """
    if session_model:
        chosen = store.get_model(session_model)
        if chosen and chosen["enabled"]:
            return chosen
    return default_model(settings)
