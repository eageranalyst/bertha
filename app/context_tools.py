"""Bertha's context tools for agents, as an MCP server: the repo's wiki, its
code map and code search, and the notes people saved (app/agent_context.py).

A turn's prompt only lists what exists; these tools read it. They are all
read-only and scoped to the session's repo by the same per-turn key as the
GitHub tools, served from the same port at /context/mcp.
"""

import asyncio
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import store
from .agent_context import page_summary
from .config import get_settings

logger = logging.getLogger("factory.context_tools")

PROTOCOL_VERSION = "2025-03-26"
SEARCH_HITS = 20
MAP_MAX_CHARS = 60_000

TOOLS: list[dict[str, Any]] = [
    {
        "name": "wiki_pages",
        "description": "List every page of this repository's wiki: slug, title, section and a one-line summary.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_page",
        "description": "Read one wiki page in full, by its slug (from wiki_pages or the page list in your instructions).",
        "inputSchema": {"type": "object", "required": ["slug"], "properties": {"slug": {"type": "string"}}},
    },
    {
        "name": "wiki_search",
        "description": "Search every wiki page for words or a phrase. Returns the matching lines with their page slugs.",
        "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "repo_map",
        "description": "Each source file with its top-level definitions, as of the last wiki build. Pass a path prefix (e.g. \"web/components\") to see one part.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "search_code",
        "description": "Find the code most relevant to a question or task, by meaning and by file and symbol names. Returns file paths, line ranges and the code.",
        "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "context_notes",
        "description": "List the notes people saved for this repository: lessons from reviews of earlier pull requests and from CI fixes. Id, kind and title.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "context_note",
        "description": "Read one saved note in full, by id: the rule, why it applies, and the review comment or CI log it came from.",
        "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
    },
]


class ToolError(Exception):
    """Reported back to the agent as a tool error."""


def _terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[\w.-]+", query.lower()) if len(t) > 1]


def call_tool(repo: str, name: str, args: dict[str, Any]) -> Any:
    if name == "wiki_pages":
        pages = store.list_wiki_pages(repo)
        if not pages:
            return "This repository has no wiki yet."
        out = []
        for page in pages:
            full = store.get_wiki_page(repo, page["slug"]) or {}
            out.append({"slug": page["slug"], "title": page["title"], "section": page["section"],
                        "summary": page_summary(full.get("body") or "")})
        return out

    if name == "wiki_page":
        page = store.get_wiki_page(repo, str(args.get("slug") or ""))
        if not page:
            slugs = ", ".join(p["slug"] for p in store.list_wiki_pages(repo)) or "none"
            raise ToolError(f"no wiki page called {args.get('slug')!r}. Pages: {slugs}")
        return f"# {page['title']}\n\n{page['body']}"

    if name == "wiki_search":
        terms = _terms(str(args.get("query") or ""))
        if not terms:
            raise ToolError("give some words to search for")
        hits = []
        for page in store.list_wiki_pages(repo):
            body = (store.get_wiki_page(repo, page["slug"]) or {}).get("body") or ""
            for number, line in enumerate(body.splitlines(), 1):
                lowered = line.lower()
                matched = sum(t in lowered for t in terms)
                if matched:
                    hits.append((matched, page["slug"], number, line.strip()))
        hits.sort(key=lambda h: -h[0])
        if not hits:
            return "No wiki page mentions that."
        return [{"slug": slug, "line": number, "text": text[:400]} for _, slug, number, text in hits[:SEARCH_HITS]]

    if name == "repo_map":
        code_map = store.get_code_map(repo)
        if not code_map or not code_map["map"]:
            return "No code map yet: it's built with the wiki."
        prefix = str(args.get("path") or "").strip().lstrip("./")
        text = code_map["map"]
        if prefix:
            # One line per file: "path: definitions".
            text = "\n".join(l for l in text.splitlines() if l.startswith(prefix)) or f"No files under {prefix}."
        if len(text) > MAP_MAX_CHARS:
            text = text[:MAP_MAX_CHARS] + "\n… (truncated: pass a path to see one part)"
        return f"As of commit {code_map['commit_sha'][:7]}:\n\n{text}"

    if name == "search_code":
        from .scheduler import relevant_code  # the scheduler imports this module's neighbours

        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("say what you're looking for")
        return relevant_code(get_settings(), repo, query) or "Nothing found: the repository has no code index yet."

    if name == "context_notes":
        notes = store.list_context_notes(repo, status="saved")
        if not notes:
            return "No saved notes for this repository."
        return [{"id": n["id"], "kind": n["kind"], "title": n["title"]} for n in notes]

    if name == "context_note":
        try:
            note = store.get_context_note(int(args.get("id")))
        except (TypeError, ValueError):
            note = None
        if not note or note["repo"] != repo or note["status"] != "saved":
            raise ToolError(f"no saved note {args.get('id')} for this repository; list them with context_notes")
        store.count_context_note_use(note["id"])
        source = note["source"]
        where = f"PR #{source.get('pr_number')}" if source.get("pr_number") else "an earlier session"
        return (f"# {note['title']}\n\n{note['body']}\n\nFrom {'a review of' if note['kind'] == 'review' else 'a CI fix on'} "
                f"{where}" + (f':\n\n> {note["quote"]}' if note["quote"] else "."))

    raise ToolError(f"unknown tool {name}")


router = APIRouter()


def _result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/context/mcp")
async def mcp(request: Request) -> Response:
    key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    grant = store.get_grant(key) if key else None
    if not grant or grant["scope"] == "model":  # a model-only key (app/model_relay.py)
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "invalid or revoked key"}},
                            status_code=401)
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                            status_code=400)
    method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if request_id is None:
        return Response(status_code=202)
    if method == "initialize":
        return JSONResponse(_result(request_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bertha-context", "version": "1.0"},
            "instructions": f"Bertha's wiki, code map, code search and saved notes for {grant['repo']}. "
                            "Wiki pages were written by a model from the code: check what matters against the code.",
        }))
    if method == "ping":
        return JSONResponse(_result(request_id, {}))
    if method == "tools/list":
        return JSONResponse(_result(request_id, {"tools": TOOLS}))
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            output = await asyncio.to_thread(call_tool, grant["repo"], name, args)
            text = output if isinstance(output, str) else json.dumps(output, indent=1, default=str)
            return JSONResponse(_result(request_id, {"content": [{"type": "text", "text": text[:60_000]}],
                                                     "isError": False}))
        except ToolError as exc:
            return JSONResponse(_result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True}))
        except Exception as exc:
            logger.exception("context tool %s failed", name)
            return JSONResponse(_result(request_id, {"content": [{"type": "text", "text": f"failed: {exc}"}],
                                                     "isError": True}))
    return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": -32601, "message": f"method not found: {method}"}})


@router.get("/context/mcp")
async def mcp_stream() -> Response:
    return Response(status_code=405)
