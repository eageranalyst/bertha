FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The app package is mounted at /src/app when a build runs (see
# DockerRunner.start_wiki_build), so the image only needs an interpreter
# and git. Changing the Python code doesn't require rebuilding this image.
WORKDIR /data
