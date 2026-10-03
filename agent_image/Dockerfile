FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg python3 python3-pip python3-venv \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY run_summary.py /opt/factory/run_summary.py

WORKDIR /data
ENTRYPOINT ["/entrypoint.sh"]
