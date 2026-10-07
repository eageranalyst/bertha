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

import hashlib
import io
import json
import logging
import tarfile
import time
from pathlib import Path
from typing import Any

import docker
from docker.errors import ImageNotFound, NotFound

logger = logging.getLogger("factory.docker_runner")

# Label on every wiki build container, so leftovers from a previous API
# process can be found and removed at startup.
WIKI_LABEL = "factory.wiki"
WIKI_REPO_LABEL = "factory.repo"
WIKI_OUTPUT_PATH = "/data/wiki.json"

# The internal network agent and wiki containers run on, and the proxy that
# is its only exit (app/egress_proxy.py).
AGENT_NETWORK = "factory-agents"
EGRESS_CONTAINER = "factory-egress"
EGRESS_HOST = "egress"
EGRESS_PROXY_PORT = 3128
EGRESS_CONFIG_LABEL = "factory.egress.config"


class DockerRunner:
    def __init__(self, image_tag: str, wiki_image_tag: str = "factory-wiki:latest", isolate: bool = True):
        self.client = docker.from_env()
        self.image_tag = image_tag
        self.wiki_image_tag = wiki_image_tag
        self.isolate = isolate

    def ensure_image(self, build_context: Path, rebuild: bool = False, tag: str | None = None) -> None:
        """Builds the image when it's missing or its build folder has changed
        since it was built, so an edited entrypoint takes effect on restart."""
        tag = tag or self.image_tag
        digest = hashlib.sha256()
        for path in sorted(p for p in build_context.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(build_context)).encode() + b"\0" + path.read_bytes())
        fingerprint = digest.hexdigest()[:16]
        if not rebuild:
            try:
                if self.client.images.get(tag).labels.get("factory.context") == fingerprint:
                    return
            except ImageNotFound:
                pass
        logger.info("Building image %s from %s", tag, build_context)
        _image, logs = self.client.images.build(path=str(build_context), tag=tag, rm=True,
                                                labels={"factory.context": fingerprint})
        for chunk in logs:
            if "stream" in chunk:
                logger.debug(chunk["stream"].rstrip())

    # -- isolation ------------------------------------------------------

    def ensure_egress(self, app_dir: Path, allow: list[str], model_host: str, model_port: int,
                      tools_port: int | None = None) -> None:
        """Creates the internal agent network and (re)starts the egress proxy
        that is its only way out. See app/egress_proxy.py. Called at startup,
        so a changed allowlist or model address takes effect on restart.
        """
        try:
            network = self.client.networks.get(AGENT_NETWORK)
        except NotFound:
            network = self.client.networks.create(AGENT_NETWORK, driver="bridge", internal=True)

        # Replacing the proxy briefly leaves Docker's DNS pointing "egress" at
        # the old container, so keep a running proxy whose config and code are
        # unchanged, and only replace it when something did change.
        forwards = f"{model_port}={model_host}:{model_port}"
        if tools_port:
            # The GitHub tool server runs on the host, next to the model server.
            forwards += f",{tools_port}={model_host}:{tools_port}"
        fingerprint = hashlib.sha256(json.dumps({
            "allow": allow, "forwards": forwards,
            "code": (app_dir / "egress_proxy.py").read_text(),
        }, sort_keys=True).encode()).hexdigest()[:16]
        try:
            existing = self.client.containers.get(EGRESS_CONTAINER)
            if existing.status == "running" and existing.labels.get(EGRESS_CONFIG_LABEL) == fingerprint:
                return
            existing.remove(force=True)
        except NotFound:
            pass
        proxy = self.client.containers.run(
            self.wiki_image_tag,
            command=["python3", "-m", "app.egress_proxy"],
            working_dir="/src",
            environment={
                "EGRESS_ALLOW": ",".join(allow),
                "FORWARDS": forwards,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            volumes={str(app_dir): {"bind": "/src/app", "mode": "ro"}},
            extra_hosts={"host.docker.internal": "host-gateway"},
            restart_policy={"Name": "unless-stopped"},
            labels={EGRESS_CONFIG_LABEL: fingerprint},
            detach=True,
            name=EGRESS_CONTAINER,
        )
        network.connect(proxy, aliases=[EGRESS_HOST])
        # Containers started before the proxy listens can't get out at all, so
        # don't return until it says it's ready.
        deadline = time.monotonic() + 30
        while b"egress proxy on" not in proxy.logs():
            if time.monotonic() > deadline:
                raise RuntimeError(f"the egress proxy didn't start: {proxy.logs().decode()[-500:]}")
            time.sleep(0.2)
        # Give Docker's DNS time to point "egress" at the new container.
        time.sleep(5)

    def isolation(self, model_port: int) -> dict[str, Any]:
        """docker-py arguments that put a container on the internal network,
        with the egress proxy as its only exit. Empty when isolation is off.
        """
        if not self.isolate:
            return {"extra_hosts": {"host.docker.internal": "host-gateway"}}
        proxy = f"http://{EGRESS_HOST}:{EGRESS_PROXY_PORT}"
        return {
            "network": AGENT_NETWORK,
            "environment": {
                "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
                "http_proxy": proxy, "https_proxy": proxy,
                "NO_PROXY": f"{EGRESS_HOST},localhost,127.0.0.1",
                "no_proxy": f"{EGRESS_HOST},localhost,127.0.0.1",
            },
            "model_base_host": f"{EGRESS_HOST}:{model_port}",
        }

    def _run(self, image: str, env: dict[str, str], limits: dict[str, int], model_port: int,
             **kwargs: Any):
        iso = self.isolation(model_port)
        environment = {**env, **iso.get("environment", {})}
        if "model_base_host" in iso and "LOCAL_MODEL_BASE_URL" in environment:
            # The model server is reached through the proxy's forward port.
            environment["LOCAL_MODEL_BASE_URL"] = f"http://{iso['model_base_host']}/v1"
        extra = {k: v for k, v in iso.items() if k in ("network", "extra_hosts")}
        return self.client.containers.run(
            image, environment=environment, detach=True, **limits, **extra, **kwargs
        )

    def start_turn(self, session_id: int, workspace: str, env: dict[str, str],
                   limits: dict[str, int], model_port: int) -> str:
        self.client.volumes.create(name=workspace)
        container = self._run(
            self.image_tag, env, limits, model_port,
            volumes={workspace: {"bind": "/data", "mode": "rw"}},
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

    def remove_volume(self, workspace: str) -> None:
        try:
            self.client.volumes.get(workspace).remove(force=True)
        except NotFound:
            pass

    def start_wiki_build(self, repo: str, env: dict[str, str], app_dir: Path,
                         limits: dict[str, int], model_port: int) -> str:
        """One wiki build in its own container. The app package is mounted
        read-only so the worker runs the same code as the API, and the
        container's own filesystem holds the output (see read_file).
        """
        name = f"factory-wiki-{repo.replace('/', '-').lower()}-{container_suffix()}"
        container = self._run(
            self.wiki_image_tag, {**env, "PYTHONDONTWRITEBYTECODE": "1"}, limits, model_port,
            command=["python3", "-m", "app.wiki_worker"],
            working_dir="/src",
            volumes={str(app_dir): {"bind": "/src/app", "mode": "ro"}},
            labels={WIKI_LABEL: "1", WIKI_REPO_LABEL: repo},
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

    def wiki_builds(self) -> list[tuple[str, str]]:
        """(repo, container id) for every wiki build container, running or
        stopped, so a restarted API can pick their output back up.
        """
        builds = []
        for container in self.client.containers.list(all=True, filters={"label": f"{WIKI_LABEL}=1"}):
            repo = container.labels.get(WIKI_REPO_LABEL)
            if repo:
                builds.append((repo, container.id))
        return builds


def container_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]
