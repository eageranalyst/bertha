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
    anthropic_api_key: str
    poll_interval_seconds: int
    sleep_after_minutes: int
    db_path: str
    agent_image_tag: str
    host: str
    port: int
    bot_name: str
    bot_email: str


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
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
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
