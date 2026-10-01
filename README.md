# Agent Factory

A lightweight local service that picks up GitHub issues assigned to a
bot account, does the work in an ephemeral Docker container, opens a
PR, then goes idle. While idle it polls the PR for new comments and
wakes itself back up to act on them. A local web UI lists every
session and lets you message, close, archive, or start one by hand.

No cloud control plane, no webhooks, no multi-tenant auth: this is a
single-user, single-machine tool. GitHub is only ever polled, never
pushes to us, so nothing needs to be publicly reachable.

## Status

**Not yet runnable end-to-end.** `app/`, the FastAPI service, and the
static UI are built and import cleanly. The agent container's
`entrypoint.sh` — the script that would actually run Claude Code
unattended with push credentials inside the container — was not
written: Claude Code's own safety classifier blocked it as "create
unsafe agents" (fully autonomous, permission-bypassing, with repo
write access, no human in the loop at execution time). See *Open
design question* below before continuing.

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
  holding the git clone and Claude Code's own session state. Sleeping
  costs nothing; waking is just another container run against the
  same volume, resuming the same Claude session (`claude --resume`).
- **State** lives in `data/factory.db` (sqlite) — sessions and a log
  of everything that happened to each one.
- **UI** (`static/`) is plain HTML/JS served by the same FastAPI app,
  polling `/api/sessions` every few seconds. No build step.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_TOKEN, GITHUB_BOT_USERNAME, FACTORY_REPOS, ANTHROPIC_API_KEY
```

Add the bot account as a collaborator on every repo in `FACTORY_REPOS`
(or give it org access) — it needs to be assignable to issues and able
to push branches / open PRs.

Docker must be running locally; the first start builds the agent image
from `agent_image/` (currently incomplete — see above).

```bash
uvicorn app.main:app --reload --port 8787
```

Open `http://127.0.0.1:8787`.

## Open design question: the agent container's entrypoint

The blocked piece is the script each container runs: clone/resume the
repo, run `claude -p "<prompt>" --permission-mode bypassPermissions`
unattended, push, and `gh pr create`. That combination — full tool
access, no permission prompts, holding a push-capable GitHub token,
with nobody watching — is exactly what the safety check exists to
catch, regardless of the Docker isolation around it.

Options to actually resolve this (pick one, or suggest another, and
I'll write the entrypoint against it):

1. **Narrow the tool surface** instead of bypassing permissions
   outright: `--permission-mode acceptEdits` plus explicit
   `--allowedTools`/`--disallowedTools` scoping Bash to a safe subset
   (package managers, test runners, git) rather than wide open shell
   access.
2. **Draft PRs only**, with a repo-side branch-protection rule (or just
   convention) that nothing from `factory/*` branches merges without
   you clicking the button — the autonomy is scoped to "propose," never
   "land."
3. **A separate, far more limited token** for the container (e.g. a
   fine-grained PAT scoped to *only* the specific repos, with Contents
   + Pull Requests write but no admin/delete/other-repo scope), so even
   a worst-case runaway agent can't do much beyond what a PR already
   bounds.

None of these are mutually exclusive — in practice you'd likely want
(2) and (3) regardless of which permission mode you pick for (1).

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
  Dockerfile        node+python+git+gh+claude-code base image
  entrypoint.sh      <- not yet written, see above
static/
  index.html, app.js, style.css   local UI, no build step
```
