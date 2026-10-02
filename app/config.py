import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _split_repos(value: str) -> list[str]:
    return [r.strip() for r in value.split(",") if r.strip()]


@dataclass(frozen=True)
class Settings:
    github_token: str
    github_bot_username: str
    repos: list[str]
    local_model_provider: str
    local_model_base_url: str
    local_model_key: str
    local_model_id: str
    poll_interval_seconds: int
    sleep_after_minutes: int
    db_path: str
    agent_image_tag: str
    host: str
    port: int
    bot_name: str
    bot_email: str

    @property
    def opencode_model_ref(self) -> str:
        """provider/model-key, what opencode's -m flag expects."""
        return f"{self.local_model_provider}/{self.local_model_key}"


@lru_cache
def get_settings() -> Settings:
    repos = _split_repos(os.environ.get("FACTORY_REPOS", ""))
    if not repos:
        raise RuntimeError(
            "FACTORY_REPOS must list at least one owner/name repo (comma-separated)"
        )
    return Settings(
        github_token=os.environ["GITHUB_TOKEN"],
        github_bot_username=os.environ["GITHUB_BOT_USERNAME"],
        repos=repos,
        # Defaults match this machine's LM Studio setup (see
        # ~/.config/opencode/opencode.jsonc). host.docker.internal is how the
        # container reaches a server bound to localhost on the host -- works
        # out of the box on Docker Desktop (macOS/Windows); on Linux Docker
        # Engine it needs the extra_hosts mapping docker_runner.py adds.
        local_model_provider=os.environ.get("FACTORY_LOCAL_PROVIDER", "lmstudio"),
        local_model_base_url=os.environ.get(
            "FACTORY_LOCAL_BASE_URL", "http://host.docker.internal:1234/v1"
        ),
        local_model_key=os.environ.get("FACTORY_LOCAL_MODEL_KEY", "qwen3.8-27b"),
        local_model_id=os.environ.get("FACTORY_LOCAL_MODEL_ID", "qwen/qwen3.8-27b"),
        poll_interval_seconds=int(os.environ.get("FACTORY_POLL_INTERVAL_SECONDS", "60")),
        sleep_after_minutes=int(os.environ.get("FACTORY_SLEEP_AFTER_MINUTES", "30")),
        db_path=os.environ.get("FACTORY_DB_PATH", "data/factory.db"),
        agent_image_tag=os.environ.get("FACTORY_AGENT_IMAGE_TAG", "factory-agent:latest"),
        host=os.environ.get("FACTORY_HOST", "127.0.0.1"),
        port=int(os.environ.get("FACTORY_PORT", "8787")),
        bot_name=os.environ.get("FACTORY_BOT_NAME", "factory-agent"),
        bot_email=os.environ.get(
            "FACTORY_BOT_EMAIL", "factory-agent@users.noreply.github.com"
        ),
    )
