"""Model providers: the local model server, and hosted vendors.

A provider's connection -- its API key and base URL -- is set in .env, because
it's a secret or rarely changes, and the Models settings page only shows it.
Which of a provider's models Bertha uses, and their tier, context and price,
are set on that page and stored with the model (app/models.py).

The local server is reached directly: by the app at FACTORY_LOCAL_HOST_BASE_URL
and by agent containers through the egress proxy. Hosted providers are called
by the app with the key, and by agent containers through the model relay
(app/model_relay.py), which adds the key, so agents never hold one.

Every hosted provider except Anthropic speaks the OpenAI API. Anthropic does
too, for the app's simple chat calls; agents use its own API, which keeps
prompt caching.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings, get_settings

logger = logging.getLogger("factory.providers")

LOCAL = "local"
ANTHROPIC_VERSION = "2023-06-01"
LIST_TIMEOUT_SECONDS = 20

# Model ids that are for something other than chat: embeddings, speech, images.
NOT_CHAT_RE = re.compile(
    r"embed|whisper|tts|dall-e|image|audio|realtime|transcribe|moderation|davinci|babbage|search|guard",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderType:
    id: str
    label: str
    # The OpenAI-style API root, ending in /v1 or its equivalent. Empty for
    # the local server and the custom provider, whose URL comes from .env.
    api_base: str
    # "anthropic" for Anthropic's own API (agents only), else "openai".
    api: str
    blurb: str


HOSTED = (
    ProviderType("anthropic", "Anthropic", "https://api.anthropic.com/v1", "anthropic",
                 "Claude models. Agents use Anthropic's own API, with prompt caching."),
    ProviderType("openai", "OpenAI", "https://api.openai.com/v1", "openai", "GPT models."),
    ProviderType("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai",
                 "One key for hundreds of models, many of them cheap. Lists each model's context and price."),
    ProviderType("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "openai", "Low-cost coding and reasoning models."),
    ProviderType("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "openai",
                 "Gemini models through Google's OpenAI-compatible endpoint. Flash models are cheap."),
    ProviderType("custom", "Custom", "", "openai",
                 "Any other OpenAI-compatible server: Groq, Together, Fireworks, Ollama, vLLM."),
)
BY_ID = {p.id: p for p in HOSTED}


def is_hosted(provider: str | None) -> bool:
    return bool(provider) and provider != LOCAL


def label(settings: Settings, provider: str) -> str:
    if provider == LOCAL:
        return "Local model server"
    if provider == "custom":
        return settings.custom_provider_name
    return BY_ID[provider].label if provider in BY_ID else provider


def api_base(settings: Settings, provider: str) -> str:
    """Where the app reaches the provider's OpenAI-style API."""
    if provider == LOCAL:
        return settings.local_model_host_base_url.rstrip("/")
    if provider == "custom":
        return settings.custom_provider_base_url
    return BY_ID[provider].api_base


def configured(settings: Settings, provider: str) -> bool:
    """Whether .env has what the provider needs. The custom server needs a
    URL, and a key only if it asks for one."""
    if provider == LOCAL:
        return True
    if provider == "custom":
        return bool(settings.custom_provider_base_url)
    return provider in BY_ID and bool(settings.provider_keys.get(provider))


def usable(model: dict[str, Any]) -> bool:
    """An enabled model whose provider is still set up. Taking a key out of
    .env takes that provider's models out of use without forgetting them."""
    return bool(model["enabled"]) and configured(get_settings(), model.get("provider") or LOCAL)


def auth_headers(settings: Settings, provider: str, native: bool = False) -> dict[str, str]:
    """The provider's credentials. `native` is Anthropic's own API, which takes
    its key in x-api-key; everything else, Anthropic's OpenAI-compatible
    endpoint included, takes a bearer token."""
    key = settings.provider_keys.get(provider, "") if is_hosted(provider) else ""
    if not key:
        return {}
    if native and provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def chat_target(settings: Settings, model: dict[str, Any] | None) -> tuple[str, dict[str, str], dict[str, Any]]:
    """(url, headers, extra request fields) for an OpenAI-style chat completion
    on this model, from the app. No model means the local server."""
    provider = (model or {}).get("provider") or LOCAL
    extra: dict[str, Any] = {}
    if provider == "anthropic":
        # Anthropic requires a reply limit; the others default to the model's.
        extra["max_tokens"] = 8192
    return f"{api_base(settings, provider)}/chat/completions", auth_headers(settings, provider), extra


# -- what a provider offers --------------------------------------------------

def available_models(settings: Settings, provider: str) -> list[dict[str, Any]]:
    """The chat models a hosted provider offers, to choose from: id, name, and
    context and prices (dollars per million tokens) where the provider says.
    Raises if the provider can't be reached."""
    if not is_hosted(provider) or not configured(settings, provider):
        raise ValueError(f"{provider} isn't set up in .env")
    native = provider == "anthropic"
    url = f"{api_base(settings, provider)}/models" + ("?limit=1000" if native else "")
    response = httpx.get(url, headers=auth_headers(settings, provider, native=native), timeout=LIST_TIMEOUT_SECONDS)
    response.raise_for_status()
    offered = []
    for m in response.json().get("data", []):
        model_id = str(m.get("id") or "").removeprefix("models/")
        if not model_id or NOT_CHAT_RE.search(model_id):
            continue
        pricing = m.get("pricing") or {}
        offered.append({
            "model_id": model_id,
            "name": m.get("display_name") or m.get("name") or model_id,
            "context_length": _int(m.get("context_length") or m.get("max_input_tokens") or m.get("context_window")),
            # OpenRouter prices per token, as strings.
            "price_in": _per_million(pricing.get("prompt")),
            "price_out": _per_million(pricing.get("completion")),
        })
    return sorted(offered, key=lambda m: m["model_id"])


def _int(value: Any) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _per_million(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return round(price * 1_000_000, 4) if price >= 0 else None


# -- the settings page --------------------------------------------------------

def _env(name: str, value: str, secret: bool = False) -> dict[str, Any]:
    """One .env setting as the settings page shows it: secrets only say whether they're set."""
    return {"name": name, "set": bool(value), "value": None if secret else (value or None)}


def describe(settings: Settings) -> dict[str, Any]:
    """Providers and the app-wide model settings from .env, for the Models page.
    Read-only: these change in .env, and take effect on restart."""
    providers = [{
        "id": LOCAL,
        "label": label(settings, LOCAL),
        "blurb": f"OpenAI-compatible server on this machine ({settings.local_model_provider}). Free; "
                 "models are discovered from it.",
        "hosted": False,
        "configured": True,
        "env": [
            _env("FACTORY_LOCAL_HOST_BASE_URL", settings.local_model_host_base_url),
            _env("FACTORY_LOCAL_BASE_URL", settings.local_model_base_url),
            _env("FACTORY_LOCAL_PROVIDER", settings.local_model_provider),
        ],
    }]
    for p in HOSTED:
        env = [_env(f"FACTORY_{p.id.upper()}_API_KEY", settings.provider_keys.get(p.id, ""), secret=True)]
        if p.id == "custom":
            env = [
                _env("FACTORY_CUSTOM_BASE_URL", settings.custom_provider_base_url),
                env[0],
                _env("FACTORY_CUSTOM_NAME", settings.custom_provider_name),
            ]
        else:
            env.append({"name": "API", "set": True, "value": p.api_base})
        providers.append({
            "id": p.id,
            "label": label(settings, p.id),
            "blurb": p.blurb,
            "hosted": True,
            "configured": configured(settings, p.id),
            "env": env,
        })
    general = [
        _env("FACTORY_ROUTING", "on" if settings.routing_enabled else "off"),
        _env("FACTORY_DEFAULT_MODEL", settings.default_model_key),
        _env("FACTORY_EMBEDDING_MODEL", settings.embedding_model_id),
    ]
    return {"providers": providers, "general": general}
