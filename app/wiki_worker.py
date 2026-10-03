"""Entry point for the wiki build container.

Runs as `python3 -m app.wiki_worker` with the app package mounted at
/src/app (see DockerRunner.start_wiki_build). Inputs come in as env vars.
The result is written to /data/wiki.json, which the API reads back from
the stopped container. Progress and failures go to stdout as
FACTORY_WIKI_PROGRESS= and FACTORY_WIKI_ERROR= lines, which the API
follows from the container logs.
"""

import json
import os
import sys
from pathlib import Path

from .wiki_build import build

RESULT_PATH = Path("/data/wiki.json")


def main() -> int:
    def progress(message: str) -> None:
        print(f"FACTORY_WIKI_PROGRESS={message}", flush=True)

    try:
        result = build(
            repo=os.environ["REPO"],
            token=os.environ["GITHUB_TOKEN"],
            known_sha=os.environ.get("KNOWN_COMMIT_SHA") or None,
            force=os.environ.get("FORCE") == "1",
            model_base_url=os.environ["LOCAL_MODEL_BASE_URL"],
            model_id=os.environ["LOCAL_MODEL_ID"],
            git_base=os.environ.get("WIKI_GIT_BASE", "https://github.com"),
            progress=progress,
        )
    except Exception as exc:
        message = (str(exc) or type(exc).__name__).replace("\n", " ")
        print(f"FACTORY_WIKI_ERROR={message}", flush=True)
        return 1

    RESULT_PATH.write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
