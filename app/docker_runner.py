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

import io
import logging
import tarfile
from pathlib import Path

import docker
from docker.errors import ImageNotFound, NotFound

logger = logging.getLogger("factory.docker_runner")

# Label on every wiki build container, so leftovers from a previous API
# process can be found and removed at startup.
WIKI_LABEL = "factory.wiki"
WIKI_OUTPUT_PATH = "/data/wiki.json"


class DockerRunner:
    def __init__(self, image_tag: str, wiki_image_tag: str = "factory-wiki:latest"):
        self.client = docker.from_env()
        self.image_tag = image_tag
        self.wiki_image_tag = wiki_image_tag

    def ensure_image(self, build_context: Path, rebuild: bool = False, tag: str | None = None) -> None:
        tag = tag or self.image_tag
        if not rebuild:
            try:
                self.client.images.get(tag)
                return
            except ImageNotFound:
                pass
        logger.info("Building image %s from %s", tag, build_context)
        _image, logs = self.client.images.build(path=str(build_context), tag=tag, rm=True)
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
            # Docker Desktop (macOS/Windows) already resolves this; on Linux
            # Docker Engine it needs this explicit mapping to reach a model
            # server bound to localhost on the host.
            extra_hosts={"host.docker.internal": "host-gateway"},
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

    def start_wiki_build(self, repo: str, env: dict[str, str], app_dir: Path) -> str:
        """One wiki build in its own container. The app package is mounted
        read-only so the worker runs the same code as the API, and the
        container's own filesystem holds the output (see read_file).
        """
        name = f"factory-wiki-{repo.replace('/', '-').lower()}-{container_suffix()}"
        container = self.client.containers.run(
            self.wiki_image_tag,
            command=["python3", "-m", "app.wiki_worker"],
            working_dir="/src",
            environment={**env, "PYTHONDONTWRITEBYTECODE": "1"},
            volumes={str(app_dir): {"bind": "/src/app", "mode": "ro"}},
            extra_hosts={"host.docker.internal": "host-gateway"},
            labels={WIKI_LABEL: "1"},
            detach=True,
            name=name,
        )
        return container.id

    def read_file(self, container_id: str, path: str = WIKI_OUTPUT_PATH) -> bytes:
        """Reads one file out of a container, running or stopped."""
        stream, _stat = self.client.containers.get(container_id).get_archive(path)
        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"{path} in the build container is not a regular file")
            return extracted.read()

    def remove_wiki_containers(self) -> None:
        for container in self.client.containers.list(all=True, filters={"label": f"{WIKI_LABEL}=1"}):
            container.remove(force=True)


def container_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]
