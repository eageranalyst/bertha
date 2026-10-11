"""The model relay: how agents reach hosted model providers without a key.

An agent's OpenCode is pointed at /llm/<provider>/ on the tools server (on the
host, reached through the egress proxy like the GitHub tools), with its turn's
key in place of an API key. The relay checks that key, checks the request is
for a model that's added and enabled on the Models page, swaps in the real
key from .env, and streams the provider's reply back. The key never enters the
container, and an agent can't run up a bill on a model nobody chose.
"""

import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import providers, store
from .config import get_settings

logger = logging.getLogger("factory.model_relay")

router = APIRouter()

# Long enough for a slow model writing a long reply.
TIMEOUT = httpx.Timeout(connect=10, read=600, write=60, pool=10)
# Request headers passed through to the provider; the rest (auth, host) are replaced.
PASS_HEADERS = ("content-type", "accept", "anthropic-version", "anthropic-beta")
# Response headers passed back; transfer framing is the relay's own.
RETURN_HEADERS = ("content-type", "retry-after", "request-id", "x-request-id")

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client


def _error(status: int, message: str) -> JSONResponse:
    # Shaped like both OpenAI's and Anthropic's errors, so OpenCode shows the message.
    return JSONResponse({"type": "error", "error": {"type": "relay_error", "message": message}}, status_code=status)


@router.post("/llm/{provider}/{path:path}")
async def relay(provider: str, path: str, request: Request) -> Response:
    key = (request.headers.get("x-api-key")
           or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    if not key or not store.get_grant(key):
        return _error(401, "invalid or revoked key")
    settings = get_settings()
    if not providers.is_hosted(provider) or not providers.configured(settings, provider):
        return _error(404, f"{provider} isn't set up in Bertha's .env")
    body = await request.body()
    try:
        model_id = json.loads(body or b"{}").get("model")
    except ValueError:
        return _error(400, "the request body isn't JSON")
    allowed = {m["model_id"] for m in store.list_models() if m["provider"] == provider and providers.usable(m)}
    if model_id not in allowed:
        return _error(403, f"{model_id} isn't an enabled {providers.label(settings, provider)} model in Bertha")

    native = providers.BY_ID[provider].api == "anthropic" if provider in providers.BY_ID else False
    headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_HEADERS}
    headers.update(providers.auth_headers(settings, provider, native=native))
    # Plain bytes back, so they can be passed on as they arrive.
    headers["accept-encoding"] = "identity"
    upstream = _http().build_request("POST", f"{providers.api_base(settings, provider)}/{path}",
                                     headers=headers, content=body)
    try:
        response = await _http().send(upstream, stream=True)
    except httpx.HTTPError as exc:
        logger.warning("model relay to %s failed: %s", provider, exc)
        return _error(502, f"couldn't reach {providers.label(settings, provider)}: {exc}")
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() in RETURN_HEADERS},
        background=BackgroundTask(response.aclose),
    )
