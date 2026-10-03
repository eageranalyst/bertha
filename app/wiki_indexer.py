"""Starts and follows a repo's wiki build, and stores the result.

A build runs in its own container (see wiki_worker.py and
DockerRunner.start_wiki_build), the same way code sessions do. This
module is the host side: it starts the container, polls its logs for
progress, reads the result file once the container has exited, and
writes the wiki to the database. Nothing is stored until a build has
produced every page, so a failed build leaves the previous wiki in place.

One build per repo at a time, each on a background thread.
"""

import json
import logging
import threading
import time
from pathlib import Path

from . import store
from .config import Settings
from .docker_runner import DockerRunner

logger = logging.getLogger("factory.wiki")

APP_DIR = Path(__file__).resolve().parent
POLL_SECONDS = 2
PROGRESS_PREFIX = "FACTORY_WIKI_PROGRESS="
ERROR_PREFIX = "FACTORY_WIKI_ERROR="


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
        with self._lock:
            if repo in self._running:
                return False
            self._running.add(repo)
        existing = store.get_wiki_index(repo)
        store.upsert_wiki_index(repo, status="indexing", progress="Starting build", error=None)
        thread = threading.Thread(
            target=self._run, args=(repo, existing, force), name=f"factory-wiki-{repo}", daemon=True
        )
        thread.start()
        return True

    def _run(self, repo: str, existing: dict | None, force: bool) -> None:
        try:
            self._build(repo, existing, force)
        except Exception as exc:
            logger.exception("wiki build for %s failed", repo)
            store.upsert_wiki_index(
                repo, status="failed", progress=None, error=str(exc) or type(exc).__name__
            )
        finally:
            with self._lock:
                self._running.discard(repo)

    def _build(self, repo: str, existing: dict | None, force: bool) -> None:
        known_sha = existing["commit_sha"] if existing and existing["status"] == "ready" else None
        container_id = self.runner.start_wiki_build(
            repo,
            env={
                "REPO": repo,
                "GITHUB_TOKEN": self.settings.github_token,
                "KNOWN_COMMIT_SHA": known_sha or "",
                "FORCE": "1" if force else "",
                "LOCAL_MODEL_BASE_URL": self.settings.local_model_base_url,
                "LOCAL_MODEL_ID": self.settings.local_model_id,
            },
            app_dir=APP_DIR,
        )
        try:
            exit_code, error = self._follow(repo, container_id)
            if exit_code != 0:
                raise RuntimeError(error or f"the build container exited with status {exit_code}")
            result = json.loads(self.runner.read_file(container_id))
        finally:
            self.runner.remove(container_id)

        if result["unchanged"]:
            logger.info("wiki for %s already current at %s", repo, result["commit_sha"][:7])
            store.upsert_wiki_index(repo, status="ready", progress=None, error=None)
            return

        store.replace_wiki_pages(repo, result["pages"])
        store.upsert_wiki_index(
            repo,
            status="ready",
            progress=None,
            error=None,
            commit_sha=result["commit_sha"],
            scan=result["scan"],
            last_indexed_at=time.time(),
        )
        logger.info("wiki for %s built at %s with %d pages", repo, result["commit_sha"][:7], len(result["pages"]))

    def _follow(self, repo: str, container_id: str) -> tuple[int, str | None]:
        """Mirrors the container's progress lines into the index row until
        it exits. Returns its exit code and the last error line it printed.
        """
        last_progress = None
        while True:
            container = self.runner.poll(container_id)
            lines = self.runner.logs(container_id).splitlines()
            progress = _last_value(lines, PROGRESS_PREFIX)
            if progress and progress != last_progress:
                store.upsert_wiki_index(repo, progress=progress)
                last_progress = progress
            if container.status == "exited":
                return container.attrs["State"]["ExitCode"], _last_value(lines, ERROR_PREFIX)
            time.sleep(POLL_SECONDS)


def _last_value(lines: list[str], prefix: str) -> str | None:
    for line in reversed(lines):
        if line.startswith(prefix):
            return line[len(prefix):]
    return None
