# Agent Factory

A lightweight local service that picks up GitHub issues assigned to a
bot account, does the work in an ephemeral Docker container, opens a
PR, then goes idle. While idle it polls the PR for new comments and
wakes itself back up to act on them. A local web UI lists every
session and lets you message, close, archive, or start one by hand.

No cloud control plane, no webhooks, no multi-tenant auth: this is a
single-user, single-machine tool. GitHub is only ever polled, never
pushes to us, so nothing needs to be publicly reachable.

The agent harness is [OpenCode](https://opencode.ai), pointed at a
local model server (e.g. LM Studio) rather than a hosted API -- see
`FACTORY_LOCAL_*` below.

## Status

Scaffolded and smoke-tested module by module (sqlite store, scheduler
state machine against fakes, API handlers, Docker image build). Not
yet run end-to-end against a real repo/issue in this environment.

The agent container's `entrypoint.sh` runs OpenCode headlessly with a
GitHub push token and no human in the loop at execution time, so it
deliberately does **not** use `--auto` ("auto-approve permissions that
are not explicitly denied (dangerous!)" per opencode's own flag
description -- the same shape of risk Claude Code's
`--permission-mode bypassPermissions` has). Instead it ships a
default-deny `permission.bash` config: a catch-all `"*": "ask"` with
specific safe patterns (`git …`, `npm …`, `pytest…`, `gh pr …`, etc.)
set to `"allow"`. Verified live against this opencode build: unmatched
or dangerous bash commands auto-reject within a few seconds when run
unattended (no TTY to answer a prompt), rather than hanging or
silently executing. `edit` is left `"allow"` (the agent's actual job,
already bounded to the checked-out repo tree); `webfetch` and
`websearch` are denied by default to shrink the unattended attack
surface -- loosen either in `agent_image/entrypoint.sh` if a repo
needs them.

That narrows *what* an unattended turn can do, but two things are
still worth doing on top of it, not done yet:

1. **A separate, narrowly-scoped GitHub token** for the container --
   a fine-grained PAT limited to Contents + Pull Requests write on
   just the repos in `FACTORY_REPOS`, no admin/delete/other-repo
   scope -- so a worst-case runaway turn can't do much beyond what a
   PR already bounds.
2. **Branch protection on the watched repos** so nothing from
   `factory/*` branches merges without a human clicking the button --
   the autonomy is scoped to "propose a PR," never "land it."

## How it works

- **Scheduler** (`app/scheduler.py`) ticks every `FACTORY_POLL_INTERVAL_SECONDS`
  on its own thread:
  1. lists open issues assigned to the bot account on each watched repo
     and creates a session for any that don't have one yet
  2. starts a container for any session that's `queued`
  3. checks `running` sessions for a finished container, reads the PR
     number out of its logs, and moves to `pr_open` or `failed`
  4. after `FACTORY_SLEEP_AFTER_MINUTES` of no activity, moves `pr_open`
     sessions to `sleeping`
  5. polls `sleeping` sessions' PRs for new comments since the last one
     seen (skipping the bot's own comments, so it never wakes itself)
     and queues a new turn with those comments as the prompt
- **Sessions never keep a container running.** Each turn is one
  `docker run` against a persistent named volume (`factory-ws-<id>`)
  holding the git clone and OpenCode's own session state (`HOME=/data`
  inside the container, so its sqlite session db lands on the volume
  too). Sleeping costs nothing; waking is just another container run
  against the same volume, resuming the same OpenCode session
  (`opencode run ... -s <session-id>`). OpenCode assigns that session
  id itself on the first turn -- the entrypoint prints it as
  `FACTORY_OPENCODE_SESSION_ID=...` and the scheduler stores it.
- **State** lives in `data/factory.db` (sqlite) — sessions and a log
  of everything that happened to each one.
- **UI** (`static/`) is plain HTML/JS served by the same FastAPI app,
  polling `/api/sessions` every few seconds. No build step.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_TOKEN, GITHUB_BOT_USERNAME, FACTORY_REPOS
```

Add the bot account as a collaborator on every repo in `FACTORY_REPOS`
(or give it org access) — it needs to be assignable to issues and able
to push branches / open PRs.

Have a local OpenAI-compatible model server running (LM Studio, etc.)
and reachable from Docker containers. Defaults assume LM Studio on
`127.0.0.1:1234` serving `qwen/qwen3.8-27b`, matching this machine's
`~/.config/opencode/opencode.jsonc`; override via `FACTORY_LOCAL_*` in
`.env` for a different model/provider/port. Inside the container the
host is reached as `host.docker.internal` (works out of the box on
Docker Desktop; on Linux Docker Engine `docker_runner.py` already adds
the `host-gateway` mapping that makes it work there too).

Docker must be running locally; the first start builds the agent image
from `agent_image/`.

```bash
uvicorn app.main:app --reload --port 8787
```

Open `http://127.0.0.1:8787`.

## Repo layout

```
app/
  config.py         env settings
  store.py          sqlite: sessions + session_logs
  github_client.py  polling: assigned issues, new PR comments
  docker_runner.py  per-turn ephemeral containers on a per-session volume
  scheduler.py      the state machine
  main.py           FastAPI routes + static UI mount
agent_image/
  Dockerfile        node+python+git+gh+opencode base image
  entrypoint.sh      clone/resume, run opencode, push, gh pr create
static/
  index.html, app.js, style.css   local UI, no build step
```
