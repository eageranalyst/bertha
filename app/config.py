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
    local_model_host_base_url: str
    local_model_key: str
    local_model_id: str
    poll_interval_seconds: int
    fast_poll_interval_seconds: int
    sleep_after_minutes: int
    db_path: str
    agent_image_tag: str
    wiki_image_tag: str
    # Registered model key to use when a session doesn't pick one.
    default_model_key: str
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
    # Optional now: repos can also be registered later through the UI
    # (stored in the `repos` table). This just seeds it at startup.
    repos = _split_repos(os.environ.get("FACTORY_REPOS", ""))
    return Settings(
        github_token=os.environ["GITHUB_TOKEN"],
        github_bot_username=os.environ["GITHUB_BOT_USERNAME"],
        repos=repos,
        # Defaults match this machine's LM Studio setup (see
        # ~/.config/opencode/opencode.jsonc). host.docker.internal is how a
        # *container* reaches a server bound to localhost on the host --
        # works out of the box on Docker Desktop, and on Linux Docker Engine
        # docker_runner.py adds the extra_hosts mapping that makes it work
        # there too. The *host* process (ask-type sessions, answered
        # directly by the scheduler with no container) uses
        # local_model_host_base_url instead, since host.docker.internal
        # doesn't resolve outside a container.
        local_model_provider=os.environ.get("FACTORY_LOCAL_PROVIDER", "lmstudio"),
        local_model_base_url=os.environ.get(
            "FACTORY_LOCAL_BASE_URL", "http://host.docker.internal:1234/v1"
        ),
        local_model_host_base_url=os.environ.get(
            "FACTORY_LOCAL_HOST_BASE_URL", "http://127.0.0.1:1234/v1"
        ),
        local_model_key=os.environ.get("FACTORY_LOCAL_MODEL_KEY", "qwen3.8-27b"),
        local_model_id=os.environ.get("FACTORY_LOCAL_MODEL_ID", "qwen/qwen3.8-27b"),
        poll_interval_seconds=int(os.environ.get("FACTORY_POLL_INTERVAL_SECONDS", "60")),
        fast_poll_interval_seconds=int(os.environ.get("FACTORY_FAST_POLL_INTERVAL_SECONDS", "3")),
        sleep_after_minutes=int(os.environ.get("FACTORY_SLEEP_AFTER_MINUTES", "30")),
        db_path=os.environ.get("FACTORY_DB_PATH", "data/factory.db"),
        agent_image_tag=os.environ.get("FACTORY_AGENT_IMAGE_TAG", "factory-agent:latest"),
        wiki_image_tag=os.environ.get("FACTORY_WIKI_IMAGE_TAG", "factory-wiki:latest"),
        default_model_key=os.environ.get("FACTORY_DEFAULT_MODEL", ""),
        host=os.environ.get("FACTORY_HOST", "127.0.0.1"),
        port=int(os.environ.get("FACTORY_PORT", "8787")),
        bot_name=os.environ.get("FACTORY_BOT_NAME", "factory-agent"),
        bot_email=os.environ.get(
            "FACTORY_BOT_EMAIL", "factory-agent@users.noreply.github.com"
        ),
    )
