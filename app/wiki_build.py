"""The wiki build itself: clone, scan, write one page per relevant topic.

This module runs inside the wiki build container (see wiki_worker.py),
so it depends only on the standard library and on wiki_scan. It knows
nothing about the database or the API. The scan decides which pages
exist (see PAGES below); each page is then written by the local model
from the files the scan pointed at.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .code_index import build_chunks, embed_chunks
from .wiki_scan import Scan, read_excerpt, repo_map, scan_repo

MAX_EVIDENCE_FILES = 12
EVIDENCE_BUDGET_CHARS = 24_000
EXCERPT_CHARS = 4_000
MODEL_TIMEOUT_SECONDS = 900
MERMAID_KINDS = (
    "flowchart", "graph", "erDiagram", "sequenceDiagram", "classDiagram",
    "stateDiagram", "gantt", "pie", "mindmap", "timeline", "journey",
)


@dataclass(frozen=True)
class PageSpec:
    slug: str
    title: str
    section: str
    brief: str
    applies: Callable[[Scan], bool]
    # Candidate files, most useful first. Duplicates and the evidence
    # budget are handled by the caller.
    evidence: Callable[[Scan], list[str]]


def _docs_named(scan: Scan, prefix: str) -> list[str]:
    return [p for p in scan.docs if PurePosixPath(p).name.lower().startswith(prefix)]


def _named(paths: list[str], *names: str) -> list[str]:
    wanted = {n.lower() for n in names}
    return [p for p in paths if PurePosixPath(p).name.lower() in wanted]


# Section order and page order both follow this tuple.
PAGES: tuple[PageSpec, ...] = (
    PageSpec(
        slug="overview",
        title="Overview",
        section="Start here",
        brief=(
            "What this codebase does and who it is for, its main parts and how they fit "
            "together at a high level. Link to every other page in the wiki."
        ),
        applies=lambda s: True,
        evidence=lambda s: _docs_named(s, "readme") + s.entrypoints + s.manifests + s.docs,
    ),
    PageSpec(
        slug="getting-started",
        title="Getting started",
        section="Start here",
        brief=(
            "How to set up a development environment and run the project: prerequisites, "
            "install commands, environment variables, how to run it locally and how to run "
            "the tests. Use copy-pasteable commands taken from the docs or scripts."
        ),
        applies=lambda s: bool(_docs_named(s, "readme") or s.tooling or s.deploy_files),
        evidence=lambda s: _docs_named(s, "readme")
            + _named(s.tooling, "makefile", "justfile", "taskfile.yml")
            + _named(s.files, ".env.example", ".env.sample")
            + s.deploy_files,
    ),
    PageSpec(
        slug="architecture",
        title="Architecture",
        section="Architecture",
        brief=(
            "The components and what each is responsible for, how they communicate (calls, "
            "queues, events, shared storage), the request or data flow for the main use case, "
            "and the long-running processes. Include a mermaid flowchart of the components."
        ),
        applies=lambda s: s.file_count > 0,
        evidence=lambda s: s.entrypoints + [d for d in s.docs if "architecture" in d.lower()]
            + s.manifests + s.deploy_files + s.docs,
    ),
    PageSpec(
        slug="stack",
        title="Languages & frameworks",
        section="Architecture",
        brief=(
            "The languages with their approximate share of the code, the frameworks and major "
            "libraries, runtime and tool versions, and what each is used for. A table works well."
        ),
        applies=lambda s: bool(s.languages),
        evidence=lambda s: s.manifests + _named(s.tooling, ".nvmrc", ".node-version",
            ".python-version", ".tool-versions") + s.deploy_files,
    ),
    PageSpec(
        slug="data-model",
        title="Data model",
        section="Architecture",
        brief=(
            "The entities or tables, their key fields, how they relate and their cardinality, "
            "where data is stored, and how schema changes are managed. Include a mermaid "
            "erDiagram when there are more than two entities."
        ),
        applies=lambda s: bool(s.data_files),
        evidence=lambda s: s.data_files,
    ),
    PageSpec(
        slug="deployment",
        title="Deployment & infrastructure",
        section="Architecture",
        brief=(
            "How the software is built, packaged and deployed, the environments that exist, "
            "what the infrastructure-as-code provisions, and the CI/CD stages. A mermaid "
            "flowchart of the pipeline helps if there is one."
        ),
        applies=lambda s: bool(s.infra or s.workflows),
        evidence=lambda s: s.deploy_files,
    ),
    PageSpec(
        slug="conventions",
        title="Conventions",
        section="Ways of working",
        brief=(
            "The coding conventions the codebase enforces or documents: formatting and linting, "
            "naming, directory layout rules, error handling, logging, and anything contributors "
            "or AI agents are told to do or avoid. Quote concrete rules from the documents."
        ),
        applies=lambda s: bool(s.tooling or _named(s.docs, "claude.md", "agents.md", "contributing.md")),
        evidence=lambda s: _named(s.docs, "claude.md", "agents.md", "contributing.md")
            + s.tooling,
    ),
    PageSpec(
        slug="workflow",
        title="Development workflow",
        section="Ways of working",
        brief=(
            "How work gets done: branching and commit conventions, pull request and review "
            "expectations, releases, and any paradigms the project documents (for example "
            "trunk-based development or test-first). Only describe what the documents or "
            "configuration actually say."
        ),
        applies=lambda s: bool(_named(s.docs, "contributing.md") or s.workflows
            or any("template" in PurePosixPath(d).name.lower() for d in s.docs)),
        evidence=lambda s: _named(s.docs, "contributing.md")
            + [d for d in s.docs if "template" in PurePosixPath(d).name.lower()]
            + s.workflows + s.docs,
    ),
    PageSpec(
        slug="testing",
        title="Testing",
        section="Ways of working",
        brief=(
            "The test frameworks and where tests live, what is covered and what is not, how to "
            "run each suite, fixture and mocking conventions, and the test gates in CI."
        ),
        applies=lambda s: bool(s.test_files),
        evidence=lambda s: sorted(s.test_files, key=lambda p: (
            not PurePosixPath(p).name.lower().startswith(("pytest", "vitest", "jest", "playwright", "conftest")),
            len(p),
        )),
    ),
    PageSpec(
        slug="decisions",
        title="Decisions",
        section="Ways of working",
        brief=(
            "A short summary of each recorded architecture or design decision: the context, the "
            "decision, its status and consequences. A table with one row per decision works well."
        ),
        applies=lambda s: bool(s.decisions),
        evidence=lambda s: s.decisions,
    ),
)


SYSTEM_PROMPT = """You write one page of a codebase wiki for engineers joining the project.

Rules:
- Output Markdown only. Do not wrap the whole answer in a code fence and do not write a top-level title; the page title is added for you. Start with a short summary paragraph, then use ## and ### headings.
- Ground every claim in the evidence. Name real files, modules, commands, config keys and environment variables. If the evidence does not show something, say it is not documented rather than guessing.
- When a diagram genuinely helps, include one fenced ```mermaid block using flowchart, erDiagram, sequenceDiagram or classDiagram. Keep it to about 25 nodes or fewer. Every flowchart node needs an id with its label in brackets: write `api["API server"] --> db[("Database")]`, never `"API server" --> "Database"`. Keep ids to letters, digits and underscores.
- Link to other wiki pages with Markdown links whose target is the page slug, for example [Data model](data-model). Only use slugs from the list you are given.
- Refer to repository files by their path in backticks, for example `app/store.py`.
- Prefer tables and short lists to long prose. Aim for roughly 300 to 900 words."""

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE_RE = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.DOTALL)
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def build(
    *,
    repo: str,
    token: str,
    known_sha: str | None,
    force: bool,
    model_base_url: str,
    model_id: str,
    git_base: str,
    stored_commits: dict[str, str],
    progress: Callable[[str], None],
    emit_meta: Callable[[dict[str, Any]], None],
    emit_page: Callable[[dict[str, Any]], None],
    write_index: Callable[[dict[str, Any]], None],
    embedding_model_id: str = "",
    has_code_index: bool = False,
) -> None:
    """Clones the repo and writes its wiki, reporting as it goes.

    emit_meta is called once with the commit and the page list (or
    unchanged=True when the commit is already built). emit_page is called
    as each page finishes, so nothing is lost if the build stops partway.
    A page already stored for this commit (`stored_commits`) is skipped.
    """
    workdir = Path(tempfile.mkdtemp(prefix="wiki-"))
    try:
        progress("Cloning repository")
        checkout = workdir / "repo"
        _clone(repo, checkout, token, git_base)
        sha = _head_sha(checkout)
        unchanged = known_sha == sha and not force
        if unchanged and has_code_index:
            emit_meta({"unchanged": True, "commit_sha": sha, "slugs": []})
            return

        progress("Scanning files")
        scan = scan_repo(checkout)
        if scan.file_count == 0:
            raise RuntimeError("the repository has no files to index")

        _build_code_index(checkout, scan, sha, model_base_url, embedding_model_id, progress, write_index)
        if unchanged:
            # The pages are current; only the code index was missing.
            emit_meta({"unchanged": True, "commit_sha": sha, "slugs": []})
            return

        specs = [spec for spec in PAGES if spec.applies(scan)]
        files = set(scan.files)
        slugs = {spec.slug for spec in specs}
        emit_meta({
            "unchanged": False,
            "commit_sha": sha,
            "scan": scan.to_dict(),
            "slugs": [spec.slug for spec in specs],
        })

        for position, spec in enumerate(specs):
            if not force and stored_commits.get(spec.slug) == sha:
                progress(f"Kept {position + 1}/{len(specs)}: {spec.title}")
                continue
            progress(f"Writing {position + 1}/{len(specs)}: {spec.title}")
            evidence = _select_evidence(checkout, scan, spec)
            raw = _write_page(model_base_url, model_id, repo, sha, scan, spec, evidence, specs)
            body = _clean_body(raw, repo, sha, slugs, files)
            body += _sources_section(repo, sha, [rel for rel, _ in evidence])
            emit_page({
                "slug": spec.slug,
                "title": spec.title,
                "section": spec.section,
                "position": position,
                "body": body,
            })
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _build_code_index(
    checkout: Path, scan: Scan, sha: str, model_base_url: str, embedding_model_id: str,
    progress: Callable[[str], None], write_index: Callable[[dict[str, Any]], None],
) -> None:
    """The repo map, and the code chunks with their embeddings, for agents.
    Without an embedding model, or if embedding fails, only the map is kept.
    """
    progress("Mapping the code")
    index: dict[str, Any] = {"commit_sha": sha, "map": repo_map(checkout, scan), "chunks": [], "error": None}
    if embedding_model_id:
        chunks = build_chunks(checkout, scan)
        progress(f"Indexing {len(chunks)} code chunks for search")
        try:
            embed_chunks(model_base_url, embedding_model_id, chunks)
            index["chunks"] = chunks
        except Exception as exc:
            index["error"] = f"embedding failed: {exc}"
    write_index(index)


# -- git ----------------------------------------------------------------

def _clone(repo: str, dest: Path, token: str, git_base: str) -> None:
    # The token goes in an HTTP header rather than the URL, so it never
    # ends up in git's own error messages or the remote config.
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    result = subprocess.run(
        [
            "git", "-c", f"http.extraHeader=Authorization: Basic {auth}",
            "clone", "--depth", "1", "--no-tags", "--quiet",
            f"{git_base}/{repo}.git", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        message = result.stderr.strip().replace(token, "***")
        raise RuntimeError(f"git clone failed: {message}")


def _head_sha(checkout: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# -- prompts and output -------------------------------------------------

def _select_evidence(root: Path, scan: Scan, spec: PageSpec) -> list[tuple[str, str]]:
    picked: list[tuple[str, str]] = []
    budget = EVIDENCE_BUDGET_CHARS
    for rel in dict.fromkeys(spec.evidence(scan)):
        if len(picked) >= MAX_EVIDENCE_FILES or budget <= 0:
            break
        excerpt = read_excerpt(root / rel, min(EXCERPT_CHARS, budget))
        if not excerpt:
            continue
        picked.append((rel, excerpt))
        budget -= len(excerpt)
    return picked


def _write_page(
    model_base_url: str, model_id: str, repo: str, sha: str, scan: Scan,
    spec: PageSpec, evidence: list[tuple[str, str]], specs: list[PageSpec],
) -> str:
    body = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(repo, sha, scan, spec, evidence, specs)},
        ],
        "stream": False,
        "temperature": 0.2,
    }).encode()
    request = urllib.request.Request(
        f"{model_base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=MODEL_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    return data["choices"][0]["message"]["content"]


def _user_prompt(
    repo: str, sha: str, scan: Scan, spec: PageSpec,
    evidence: list[tuple[str, str]], specs: list[PageSpec],
) -> str:
    def join(items: list[str], limit: int = 15) -> str:
        if not items:
            return "none"
        shown = ", ".join(f"`{i}`" for i in items[:limit])
        more = f" and {len(items) - limit} more" if len(items) > limit else ""
        return shown + more

    languages = ", ".join(f"{l['name']} {l['percent']}%" for l in scan.languages) or "none"
    siblings = "\n".join(f"- {p.slug} — {p.title}" for p in specs if p.slug != spec.slug) or "- none"
    lines = [
        f"Repository: {repo} (commit {sha[:7]})",
        f"Page: {spec.title} (slug: {spec.slug})",
        f"What this page should cover: {spec.brief}",
        "",
        "Other pages in this wiki (link to them by slug where relevant):",
        siblings,
        "",
        "## Scan",
        f"- Languages (share of source bytes): {languages}",
        f"- Ecosystems: {join(scan.ecosystems) if scan.ecosystems else 'none'}",
        f"- Frameworks and libraries: {', '.join(scan.frameworks) or 'none detected'}",
        f"- Infrastructure: {', '.join(scan.infra) or 'none detected'}",
        f"- Entrypoints: {join(scan.entrypoints)}",
        f"- Manifests: {join(scan.manifests)}",
        "",
        "## Layout",
        scan.tree or "(empty)",
        "",
        "## Evidence",
    ]
    if not evidence:
        lines.append("No files matched this page. Work from the scan and layout above.")
    for rel, text in evidence:
        lines += [f"### `{rel}`", "````", text, "````", ""]
    return "\n".join(lines)


def _clean_body(raw: str, repo: str, sha: str, slugs: set[str], files: set[str]) -> str:
    body = THINK_RE.sub("", raw).strip()

    # Some models wrap the whole answer in a markdown fence.
    fenced = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", body, re.DOTALL)
    if fenced:
        body = fenced.group(1).strip()

    # The page title is rendered by the UI, so drop a leading H1.
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).strip()

    def mermaid_fence(match: re.Match) -> str:
        code = match.group(1).strip()
        first = code.splitlines()[0].strip() if code else ""
        if first.startswith(MERMAID_KINDS):
            return f"```mermaid\n{code}\n```"
        return f"```text\n{code}\n```"

    body = FENCE_RE.sub(mermaid_fence, body)

    def link(match: re.Match) -> str:
        text, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            return match.group(0)
        if target.startswith("#"):
            return text
        path = target[2:] if target.startswith("./") else target.lstrip("/")
        slug = path.removesuffix(".md")
        if slug in slugs:
            return f"[{text}]({slug})"
        if path in files:
            return f"[{text}](https://github.com/{repo}/blob/{sha}/{path})"
        # Neither a page nor a file in this repo: keep the words, drop the dead link.
        return text

    return LINK_RE.sub(link, body)


def _sources_section(repo: str, sha: str, paths: list[str]) -> str:
    if not paths:
        return ""
    items = "\n".join(f"- [`{p}`](https://github.com/{repo}/blob/{sha}/{p})" for p in paths)
    return f"\n\n## Sources\n\n{items}\n"
