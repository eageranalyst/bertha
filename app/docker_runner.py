"""Docker orchestration.

Design choice: a "session" is never a long-lived container. Each turn
(initial work, or a wake-up) is one ephemeral `docker run`, and the
only thing that persists between turns is a named volume holding the
git clone and Claude Code's own session state (mounted at /data, with
HOME=/data so `claude --resume` can find its transcript next turn).
That means "sleeping" costs nothing to maintain -- there's no
container to keep alive or clean up -- and "waking" is just running a
new container against the same volume.
"""

import logging
from pathlib import Path

import docker
from docker.errors import ImageNotFound, NotFound

logger = logging.getLogger("factory.docker_runner")


class DockerRunner:
    def __init__(self, image_tag: str):
        self.client = docker.from_env()
        self.image_tag = image_tag

    def ensure_image(self, build_context: Path, rebuild: bool = False) -> None:
        if not rebuild:
            try:
                self.client.images.get(self.image_tag)
                return
            except ImageNotFound:
                pass
        logger.info("Building agent image %s from %s", self.image_tag, build_context)
        _image, logs = self.client.images.build(
            path=str(build_context), tag=self.image_tag, rm=True
        )
        for chunk in logs:
            if "stream" in chunk:
                logger.debug(chunk["stream"].rstrip())

    def volume_name(self, session_id: int) -> str:
        return f"factory-ws-{session_id}"

    def start_turn(self, session_id: int, env: dict[str, str]) -> str:
        volume = self.volume_name(session_id)
        self.client.volumes.create(name=volume)
        container = self.client.containers.run(
            self.image_tag,
            environment=env,
            volumes={volume: {"bind": "/data", "mode": "rw"}},
            detach=True,
            name=f"factory-turn-{session_id}-{container_suffix()}",
        )
        return container.id

    def poll(self, container_id: str):
        container = self.client.containers.get(container_id)
        container.reload()
        return container

    def logs(self, container_id: str) -> str:
        return self.client.containers.get(container_id).logs().decode(errors="replace")

    def remove(self, container_id: str) -> None:
        try:
            self.client.containers.get(container_id).remove(force=True)
        except NotFound:
            pass

    def remove_volume(self, session_id: int) -> None:
        try:
            self.client.volumes.get(self.volume_name(session_id)).remove(force=True)
        except NotFound:
            pass


def container_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]
