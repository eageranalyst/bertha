"""The model registry: discovery, tiers, and choosing a model.

The local model server (LM Studio, or anything OpenAI-compatible) reports what
it has loaded or can load. Those are registered here with a tier inferred from
their size. Hosted models (app/providers.py) are added by hand from what their
provider offers. Tier, description, and for hosted models context and price,
can be edited afterwards. A session records the model it was created with; a
turn resolves that to a registered, enabled model, and falls back to the
default when there isn't one.
"""

import logging
import re
from typing import Any

import httpx

from . import providers, store
from .config import Settings

logger = logging.getLogger("factory.models")

TIERS = ("light", "standard", "strong")
EMBEDDING_RE = re.compile(r"embed", re.IGNORECASE)
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b(?![a-z])", re.IGNORECASE)
# Hosted models rarely say their size, but their names say their class.
LIGHT_NAME_RE = re.compile(r"(?<![a-z])(?:haiku|mini|nano|flash|lite|small)(?![a-z])")
STRONG_NAME_RE = re.compile(r"(?<![a-z])(?:opus|pro|reasoner|large|max)(?![a-z])|\bo[134]\b")


def key_for(model_id: str) -> str:
    """The short name a model is registered under: the id without its publisher."""
    return model_id.split("/")[-1]


def infer_tier(model_id: str) -> str:
    """A starting tier from the parameter count in the name ("qwen3-27b" is
    strong, "llama-3.2-3b" is light). Unknown sizes are standard. Editable.
    """
    name = key_for(model_id).lower()
    if LIGHT_NAME_RE.search(name):
        return "light"
    if STRONG_NAME_RE.search(name):
        return "strong"
    match = SIZE_RE.search(name)
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
    registered = {m["key"]: m for m in store.list_models()}
    for model_id in discover(settings):
        key = key_for(model_id)
        if key in registered and registered[key]["provider"] != providers.LOCAL:
            key = f"{key}-local"  # the name is taken by a hosted model
        store.add_discovered_model(key, model_id, infer_tier(model_id))
    refresh_context(settings)
    return store.list_models()


def add_hosted(settings: Settings, provider: str, model_id: str) -> dict[str, Any]:
    """Registers one of a hosted provider's models, with the context and price
    the provider lists for it. Raises ValueError if it can't be added."""
    if not providers.is_hosted(provider) or not providers.configured(settings, provider):
        raise ValueError(f"{provider} isn't set up in .env")
    registered = store.list_models()
    if any(m["provider"] == provider and m["model_id"] == model_id for m in registered):
        raise ValueError(f"{model_id} is already added")
    offered = next((m for m in providers.available_models(settings, provider) if m["model_id"] == model_id), None)
    if offered is None:
        raise ValueError(f"{providers.label(settings, provider)} doesn't offer {model_id}")
    key = key_for(model_id)
    if any(m["key"] == key for m in registered):
        key = f"{provider}-{key}"
    if any(m["key"] == key for m in registered):
        raise ValueError(f"a model named {key} is already registered")
    store.add_model(key, provider, model_id, infer_tier(model_id), offered["context_length"],
                    offered["price_in"], offered["price_out"])
    return store.get_model(key)


def refresh_context(settings: Settings) -> None:
    """Records each model's context size from LM Studio's native API: the
    loaded context when the model is loaded (that's what a request gets),
    otherwise its maximum. Servers without that API leave it unknown, and
    an unreachable server leaves the stored values as they were.
    """
    native = settings.local_model_host_base_url.rstrip("/").removesuffix("/v1") + "/api/v0/models"
    try:
        response = httpx.get(native, timeout=5)
        response.raise_for_status()
        reported = response.json().get("data", [])
    except Exception:
        return
    by_id = {
        m.get("id"): (m.get("loaded_context_length") or m.get("max_context_length"), m.get("state") == "loaded")
        for m in reported
    }
    for model in store.list_models():
        if model["provider"] == providers.LOCAL and model["model_id"] in by_id:
            context, loaded = by_id[model["model_id"]]
            store.set_model_context(model["key"], context, loaded)


def availability(model: dict[str, Any]) -> int:
    """Sort key: loaded models first, unknown next, not-loaded last. Loading a
    model can fail for lack of memory, and costs time even when it works.
    Hosted models are always ready, so they count as loaded.
    """
    if providers.is_hosted(model.get("provider")):
        return 0
    return {1: 0, None: 1, 0: 2}.get(model.get("loaded"), 1)


def cost(model: dict[str, Any]) -> float:
    """Dollars per million tokens, in and out; zero for a local model."""
    return (model.get("price_in") or 0) + (model.get("price_out") or 0)


def preference(model: dict[str, Any]) -> tuple[int, bool, bool, float, str]:
    """How models in the same tier are ranked: ready first, then free, then
    cheapest. A loaded local model beats a hosted one, and a hosted one beats
    waiting on a local model to load. A hosted model with no price set comes
    after the priced ones, since it may be the dear one."""
    hosted = providers.is_hosted(model.get("provider"))
    unpriced = hosted and model.get("price_in") is None and model.get("price_out") is None
    return availability(model), hosted, unpriced, cost(model), model["key"]


def default_model(settings: Settings, local_only: bool = False) -> dict[str, Any] | None:
    """FACTORY_DEFAULT_MODEL if it names an enabled model; otherwise the
    strongest enabled model. `local_only` is for work that can only reach the
    local server (wiki builds).
    """
    enabled = [m for m in store.list_models()
               if providers.usable(m) and (not local_only or m["provider"] == providers.LOCAL)]
    if settings.default_model_key:
        pinned = next((m for m in enabled if m["key"] == settings.default_model_key), None)
        if pinned:
            return pinned
    for tier in reversed(TIERS):
        in_tier = sorted((m for m in enabled if m["tier"] == tier), key=preference)
        if in_tier:
            return in_tier[0]
    return None


def resolve(settings: Settings, session_model: str | None) -> dict[str, Any] | None:
    """The model a turn should run on: the session's own, if it's still
    registered and enabled, otherwise the default.
    """
    if session_model:
        chosen = store.get_model(session_model)
        if chosen and providers.usable(chosen):
            return chosen
    return default_model(settings)
