"""Starts, follows and finishes a repo's wiki build.

A build runs in its own container (see wiki_worker.py and
DockerRunner.start_wiki_build). This module is the host side. It follows
the container's log, storing each page as the worker reports it, so a
build that stops partway keeps the pages it finished. The next build
skips those pages when they were written from the same commit.

On startup, resume() reattaches to build containers that outlived the
previous API process, so an API restart doesn't lose a build.

One build per repo at a time, each on a background thread.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from docker.errors import NotFound

from . import agent_settings, store
from . import models as model_registry
from .config import Settings
from .docker_runner import DockerRunner

logger = logging.getLogger("factory.wiki")

APP_DIR = Path(__file__).resolve().parent
POLL_SECONDS = 2
PREFIXES = {
    "progress": "FACTORY_WIKI_PROGRESS=",
    "meta": "FACTORY_WIKI_META=",
    "page": "FACTORY_WIKI_PAGE=",
    "error": "FACTORY_WIKI_ERROR=",
    "done": "FACTORY_WIKI_DONE=",
    "index": "FACTORY_WIKI_INDEX=",
}
INDEX_PATH = "/data/code_index.json"


@dataclass
class _BuildState:
    seen: int = 0  # log lines already applied
    meta: dict | None = None
    saved: int = 0
    error: str | None = None
    done: bool = False
    index_ready: bool = False
    index_saved: bool = False
    # When Bertha started following the build: merges noticed before this are
    # in what it read, later ones still leave the wiki out of date.
    started: float = field(default_factory=time.time)


class WikiIndexer:
    def __init__(self, settings: Settings, runner: DockerRunner):
        self.settings = settings
        self.runner = runner
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def start(self, repo: str, force: bool = False) -> bool:
        """Starts a build in the background. Returns False if one is already
        running for this repo. The status flips to 'indexing' before this
        returns, so a caller that re-reads the index sees it.
        """
        if not self._claim(repo):
            return False
        existing = store.get_wiki_index(repo)
        store.upsert_wiki_index(repo, status="indexing", progress="Starting build", error=None)
        threading.Thread(
            target=self._run, args=(repo, existing, force), name=f"factory-wiki-{repo}", daemon=True
        ).start()
        return True

    def resume(self) -> None:
        """Reattaches to build containers left by a previous API process."""
        for repo, container_id in self.runner.wiki_builds():
            if not self._claim(repo):
                continue
            store.upsert_wiki_index(repo, status="indexing", progress="Reattached after a restart", error=None)
            threading.Thread(
                target=self._guarded, args=(repo, lambda: self._follow(repo, container_id)),
                name=f"factory-wiki-{repo}", daemon=True,
            ).start()

    def _claim(self, repo: str) -> bool:
        with self._lock:
            if repo in self._running:
                return False
            self._running.add(repo)
            return True

    def _run(self, repo: str, existing: dict | None, force: bool) -> None:
        known_sha = existing["commit_sha"] if existing and existing["status"] == "ready" else None

        def build() -> None:
            container_id = self.runner.start_wiki_build(
                repo,
                env={
                    "REPO": repo,
                    "GITHUB_TOKEN": self.settings.github_token,
                    "KNOWN_COMMIT_SHA": known_sha or "",
                    "FORCE": "1" if force else "",
                    "STORED_COMMITS": json.dumps(store.wiki_page_commits(repo)),
                    "LOCAL_MODEL_BASE_URL": self.settings.local_model_base_url,
                    "LOCAL_MODEL_ID": self._model_id(),
                    "EMBEDDING_MODEL_ID": self.settings.embedding_model_id,
                    "HAS_CODE_INDEX": "1" if store.get_code_map(repo) else "",
                },
                app_dir=APP_DIR,
                limits=agent_settings.container_limits(),
                model_port=self.settings.model_host_port[1],
            )
            self._follow(repo, container_id)

        self._guarded(repo, build)

    def _model_id(self) -> str:
        """The model to write pages with: the registry's default, which
        prefers a loaded model, so the build doesn't fail on one that was
        unloaded. Falls back to FACTORY_LOCAL_MODEL_ID with no registry.
        """
        model_registry.refresh_context(self.settings)
        chosen = model_registry.default_model(self.settings)
        return chosen["model_id"] if chosen else self.settings.local_model_id

    def _guarded(self, repo: str, work) -> None:
        try:
            work()
        except Exception as exc:
            logger.exception("wiki build for %s failed", repo)
            store.upsert_wiki_index(repo, status="failed", progress=None, error=str(exc) or type(exc).__name__)
        finally:
            with self._lock:
                self._running.discard(repo)

    def _follow(self, repo: str, container_id: str) -> None:
        state = _BuildState()
        while True:
            try:
                container = self.runner.poll(container_id)
            except NotFound:
                raise RuntimeError("the build container disappeared before it finished")
            lines = self.runner.logs(container_id).splitlines()
            for line in lines[state.seen:]:
                self._apply(repo, line, state)
            state.seen = len(lines)
            if state.index_ready and not state.index_saved:
                # Code search is useful before the pages are written, so the
                # index is saved as soon as the worker has it.
                self._save_index(repo, container_id, state)
            if container.status == "exited":
                exit_code = container.attrs["State"]["ExitCode"]
                break
            time.sleep(POLL_SECONDS)
        if state.index_ready and not state.index_saved:
            self._save_index(repo, container_id, state)
        self._finish(repo, state, exit_code)
        self.runner.remove(container_id)

    def _save_index(self, repo: str, container_id: str, state: "_BuildState") -> None:
        """Stores the code index the worker wrote, whatever happens to the pages."""
        state.index_saved = True
        try:
            store.save_code_index(repo, json.loads(self.runner.read_file(container_id, INDEX_PATH)))
        except Exception:
            logger.exception("could not save the code index for %s", repo)

    def _apply(self, repo: str, line: str, state: _BuildState) -> None:
        """Applies one line of the worker's output. Re-applying a line is
        harmless (pages are upserts), which is what makes resume() safe.
        """
        if line.startswith(PREFIXES["progress"]):
            store.upsert_wiki_index(repo, progress=line[len(PREFIXES["progress"]):])
        elif line.startswith(PREFIXES["meta"]):
            state.meta = json.loads(line[len(PREFIXES["meta"]):])
        elif line.startswith(PREFIXES["page"]) and state.meta:
            page = json.loads(line[len(PREFIXES["page"]):])
            store.save_wiki_page(repo, page, state.meta["commit_sha"])
            state.saved += 1
        elif line.startswith(PREFIXES["error"]):
            state.error = line[len(PREFIXES["error"]):]
        elif line.startswith(PREFIXES["done"]):
            state.done = True
        elif line.startswith(PREFIXES["index"]):
            state.index_ready = True

    def _finish(self, repo: str, state: _BuildState, exit_code: int) -> None:
        if state.done and not state.error and exit_code == 0 and state.meta:
            store.clear_wiki_dirty(repo, before=state.started)
            if state.meta["unchanged"]:
                logger.info("wiki for %s already current at %s", repo, state.meta["commit_sha"][:7])
                store.upsert_wiki_index(repo, status="ready", progress=None, error=None)
                return
            store.prune_wiki_pages(repo, state.meta["slugs"])
            store.upsert_wiki_index(
                repo,
                status="ready",
                progress=None,
                error=None,
                commit_sha=state.meta["commit_sha"],
                scan=state.meta["scan"],
                last_indexed_at=time.time(),
            )
            logger.info("wiki for %s built at %s", repo, state.meta["commit_sha"][:7])
            return

        reason = state.error or f"the build container exited with status {exit_code}"
        if state.meta and not state.meta["unchanged"]:
            total = len(state.meta["slugs"])
            reason += f" ({state.saved} of {total} pages saved; the next build keeps them)"
        store.upsert_wiki_index(repo, status="failed", progress=None, error=reason)
