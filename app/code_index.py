"""Code search for agents: chunk a repo, embed the chunks, find the ones that
match a task.

The wiki build container chunks and embeds a repo's source (build_chunks and
embed run there, so this module is standard library only). Bertha stores the
result, and at the start of each code turn embeds the task and puts the
closest chunks in the agent's first message (relevant_code). For a local
model that replaces many search steps with a few hundred tokens.

Embeddings come from the model server's /embeddings endpoint. The nomic
models expect a "search_document: " or "search_query: " prefix.
"""

import array
import json
import math
import re
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from .wiki_scan import LANGUAGES, Scan, read_excerpt

CHUNK_LINES = 40
MAX_CHUNKS = 3000
MAX_FILE_BYTES = 100_000
EMBED_CHARS = 1500
BATCH = 32
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock"}
INDEXED_SUFFIXES = set(LANGUAGES) | {".md"}


def build_chunks(root: Path, scan: Scan) -> list[dict[str, Any]]:
    """Source and markdown files split into fixed windows of lines. Tests come
    after other code, so the cap drops them first.
    """
    tests = set(scan.test_files)
    files = [
        f for f in scan.files
        if PurePosixPath(f).suffix.lower() in INDEXED_SUFFIXES
        and PurePosixPath(f).name.lower() not in SKIP_NAMES
        and not f.endswith((".min.js", ".min.css"))
    ]
    files.sort(key=lambda f: (f in tests, f))
    chunks: list[dict[str, Any]] = []
    for rel in files:
        path = root / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        text = read_excerpt(path, MAX_FILE_BYTES)
        if not text.strip():
            continue
        lines = text.splitlines()
        for start in range(0, len(lines), CHUNK_LINES):
            body = "\n".join(lines[start:start + CHUNK_LINES])
            if body.strip():
                chunks.append({"path": rel, "start": start + 1,
                               "end": min(start + CHUNK_LINES, len(lines)), "text": body})
            if len(chunks) >= MAX_CHUNKS:
                return chunks
    return chunks


def embed(base_url: str, model_id: str, texts: list[str], timeout: int = 120) -> list[list[float]]:
    """Embeddings for `texts`, in order, normalised to unit length."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        request = urllib.request.Request(
            f"{base_url}/embeddings",
            data=json.dumps({"model": model_id, "input": texts[i:i + BATCH]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = sorted(json.loads(response.read())["data"], key=lambda d: d["index"])
        vectors.extend(normalise(d["embedding"]) for d in data)
    return vectors


def embed_chunks(base_url: str, model_id: str, chunks: list[dict[str, Any]]) -> None:
    """Adds an "embedding" (a list of floats) to each chunk."""
    texts = [f"search_document: {c['path']}\n{c['text'][:EMBED_CHARS]}" for c in chunks]
    for chunk, vector in zip(chunks, embed(base_url, model_id, texts)):
        chunk["embedding"] = vector


def normalise(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / length for x in vector]


def to_blob(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def from_blob(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


STOPWORDS = {
    "the", "and", "for", "are", "with", "that", "this", "from", "into", "when", "what", "how",
    "there", "not", "its", "can", "add", "new", "make", "use", "using", "should", "would",
    "before", "after", "about", "which", "where", "why", "fix", "bug", "issue", "loading",
}
PATH_WEIGHT = 0.12
TEXT_WEIGHT = 0.04
TEST_PENALTY = 0.05


def is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (any(p in ("tests", "test", "__tests__", "e2e", "spec") for p in parts[:-1])
            or name.startswith("test_") or ".test." in name or ".spec." in name or name.endswith("_test.go"))


def matching_files(repo_map: str, terms: list[str], limit: int = 10) -> list[str]:
    """Repo map lines whose path or definitions contain task words, best
    first: a path match counts double, tests last. Names are what embeddings
    handle worst, and the map already has them.
    """
    if not terms:
        return []
    lines = [line for line in repo_map.splitlines() if not line.startswith("…")]
    lowered = [line.lower() for line in lines]
    # A word found in many files ("event" in an events app) says little about
    # which file to open; a rare one ("dedup") says a lot.
    weight = {
        t: math.log((len(lines) + 1) / (1 + sum(t in line for line in lowered)))
        for t in terms
    }
    scored = []
    for line in lines:
        path, _, symbols = line.partition(": ")
        lowered_path, lowered_symbols = path.lower(), symbols.lower()
        score = sum(weight[t] * (2 * (t in lowered_path) + (t in lowered_symbols)) for t in terms)
        if score > 0:
            if is_test_path(path):
                score *= 0.5
            scored.append((score, line))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [line for _, line in scored[:limit]]


def query_terms(task: str) -> list[str]:
    """Distinctive words from the task, cut to a 5-letter stem so that
    "deduplicated" matches "dedup" and "plugins" matches "plugin"."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", task.lower())
    return list(dict.fromkeys(w[:5] for w in words if w not in STOPWORDS))


def top_chunks(
    query: list[float], chunks: list[dict[str, Any]], k: int = 8, terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The k chunks that best match the task: cosine similarity of the
    embeddings (both unit length, so a dot product), plus a boost for task
    words in the chunk's path and text. Embeddings alone miss names and
    identifiers, which is often what matters in code.
    """
    terms = terms or []
    wants_tests = any(t.startswith("test") for t in terms)
    scored = []
    for chunk in chunks:
        score = sum(a * b for a, b in zip(query, chunk["vector"]))
        path = chunk["path"].lower()
        if terms:
            text = chunk["text"].lower()
            score += PATH_WEIGHT * sum(t in path for t in terms) / len(terms)
            score += TEXT_WEIGHT * sum(t in text for t in terms) / len(terms)
        if not wants_tests and is_test_path(path):
            score -= TEST_PENALTY  # tests describe code; the code itself comes first
        scored.append((score, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [dict(chunk, score=round(score, 3)) for score, chunk in scored[:k]]


def format_relevant(chunks: list[dict[str, Any]], max_chars: int = 7000) -> str:
    """The matched chunks as markdown, grouped by file in the order found,
    with neighbouring windows of one file merged."""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk["path"], []).append(chunk)
    parts: list[str] = []
    used = 0
    for path, found in by_file.items():
        found.sort(key=lambda c: c["start"])
        merged: list[dict[str, Any]] = []
        for chunk in found:
            if merged and chunk["start"] <= merged[-1]["end"] + 1:
                merged[-1] = {**merged[-1], "end": chunk["end"], "text": merged[-1]["text"] + "\n" + chunk["text"]}
            else:
                merged.append(dict(chunk))
        for chunk in merged:
            block = f"### `{path}` (lines {chunk['start']}-{chunk['end']})\n```\n{chunk['text']}\n```\n"
            if used + len(block) > max_chars:
                return "\n".join(parts)
            parts.append(block)
            used += len(block)
    return "\n".join(parts)
