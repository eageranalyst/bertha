"""Entry point for the wiki build container.

Runs as `python3 -m app.wiki_worker` with the app package mounted at
/src/app (see DockerRunner.start_wiki_build). Inputs come in as env vars.

Everything the API needs goes to stdout as prefixed lines, which the
API follows from the container logs and stores as it arrives:

    FACTORY_WIKI_PROGRESS=<text>    what it is doing now
    FACTORY_WIKI_META=<json>        the commit, scan and page list
    FACTORY_WIKI_PAGE=<json>        one finished page
    FACTORY_WIKI_ERROR=<text>       why it stopped
    FACTORY_WIKI_DONE=1             the build finished

Because pages are emitted one at a time, a build that stops partway
keeps the pages it finished.
"""

import json
import os
import sys
from typing import Any

from .wiki_build import build


def _emit(prefix: str, payload: dict[str, Any]) -> None:
    print(prefix + json.dumps(payload), flush=True)


def main() -> int:
    try:
        build(
            repo=os.environ["REPO"],
            token=os.environ["GITHUB_TOKEN"],
            known_sha=os.environ.get("KNOWN_COMMIT_SHA") or None,
            force=os.environ.get("FORCE") == "1",
            model_base_url=os.environ["LOCAL_MODEL_BASE_URL"],
            model_id=os.environ["LOCAL_MODEL_ID"],
            git_base=os.environ.get("WIKI_GIT_BASE", "https://github.com"),
            stored_commits=json.loads(os.environ.get("STORED_COMMITS") or "{}"),
            progress=lambda message: print(f"FACTORY_WIKI_PROGRESS={message}", flush=True),
            emit_meta=lambda meta: _emit("FACTORY_WIKI_META=", meta),
            emit_page=lambda page: _emit("FACTORY_WIKI_PAGE=", page),
        )
    except Exception as exc:
        message = (str(exc) or type(exc).__name__).replace("\n", " ")
        print(f"FACTORY_WIKI_ERROR={message}", flush=True)
        return 1

    print("FACTORY_WIKI_DONE=1", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
