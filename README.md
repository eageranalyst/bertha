# Agent Factory

A lightweight local service that picks up GitHub issues assigned to a
bot account, does the work in an ephemeral Docker container, opens a
PR, then goes idle. While idle it polls the PR for new comments and
wakes itself back up to act on them. A local web UI (Next.js, its own
process at `web/`) lists every session and lets you message, stop,
close, archive, or start one by hand.

**Creating a session is a two-step, nothing-saved-until-you-commit
flow**: click "+ New session" and a draft appears on the left (purely
client-side -- nothing on the server yet) while a compose box opens on
the right. Type a prompt, optionally pick a repo, then hit **Ask** or
**Code** -- that's the one API call that actually creates the session
and kicks off the first turn. Click away to a different session
instead, and the draft just disappears; nothing was ever saved.

Two session types:
- **ask** -- no repo, no container. The prompt (plus that session's
  own history) is proxied straight to the local model over HTTP from
  a background thread; fastest path, nothing to set up.
- **code** -- a container against a persistent per-session volume,
  full filesystem + OpenCode. A repo is optional even here: without
  one it's just a scratch workspace (no git remote, no push, no PR).

There's no issue field in the UI -- mention an issue number in the
prompt text if it's relevant. (A bot-assigned GitHub issue still
creates its own `code` session automatically via the discovery loop,
independent of the manual creation flow -- see below.)

Each session gets a short `ref` (e.g. `a1b2c3d`) and a `title`
derived from its opening prompt, shown in the left-hand list alongside
status and any associated PR, ordered by whichever happened most
recently -- a user message, a model response, or a status change.

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
  1. lists open issues assigned to the bot account on each repo in the
     registry (seeded from `FACTORY_REPOS`, extendable from the UI) and
     creates a `code` session for any that don't have one yet
  2. any `queued` session launches a turn: `ask` sessions get answered
     directly by a background thread (an HTTP call to the local
     model, no container); `code` sessions get a container (repo
     optional -- see below)
  3. checks `running` code sessions for a finished container, reads
     the PR number out of its logs, and moves to `pr_open` (repo
     attached, PR opened), `idle` (no repo attached, nothing to push),
     or `failed`
  4. after `FACTORY_SLEEP_AFTER_MINUTES` of no activity, moves `pr_open`
     sessions to `sleeping`
  5. polls `sleeping` sessions' PRs for new comments since the last one
     seen (skipping the bot's own comments, so it never wakes itself)
     and queues a new turn with those comments as the prompt
- **A session with a pending message is always what drives the next
  turn** -- a fresh issue's body, the opening prompt from creation, a
  UI message, or a new PR comment all just set `pending_message` and
  let the same promotion logic pick it up on the next tick.
- **Code sessions never keep a container running.** Each turn is one
  `docker run` against a persistent named volume (`factory-ws-<id>`)
  holding the git clone (if any) and OpenCode's own session state
  (`HOME=/data` inside the container, so its sqlite session db lands
  on the volume too). Sleeping costs nothing; waking is just another
  container run against the same volume, resuming the same OpenCode
  session (`opencode run ... -s <session-id>`). OpenCode assigns that
  session id itself on the first turn -- the entrypoint prints it as
  `FACTORY_OPENCODE_SESSION_ID=...` and the scheduler stores it.
- **Stopping a turn**: `code` sessions are killed immediately (the
  container is force-removed). `ask` sessions run their HTTP call on
  its own `httpx.Client`; stopping closes that client from the API
  handler's thread, which raises inside the worker thread making the
  request -- it catches that, logs it, and marks the session
  `stopped`.
- **State** lives in `data/factory.db` (sqlite) — sessions, a log of
  everything that happened to each one, and the repo registry.
- **UI** (`web/`) is a separate Next.js/TypeScript app, its own process
  on its own port, polling the FastAPI backend's `/api/sessions` every
  few seconds over CORS (`app/main.py` allows `localhost:3000`). Model
  responses and user messages render as Markdown with syntax
  highlighting (`react-markdown` + `rehype-highlight`); raw container
  logs (`agent_output`) render as plain preformatted text instead,
  since that's an NDJSON dump, not prose.

## Repos and wiki

The **Repos** tab lists every registered repo. Each one has two
subtabs:

- **Metadata** -- what GitHub reports about the repo (visibility,
  default branch, last push), how many sessions it has, and what the
  last codebase scan found.
- **Wiki** -- a browsable set of pages about the codebase: an
  overview, architecture, languages and frameworks, data model,
  deployment, conventions, development workflow, getting started,
  testing, and decisions. Each page is written from what the scan
  found in that repo, so a page only appears when there is something
  to write it from (no `data-model` page for a repo with no schema
  files). Pages link to each other, link repo files to their GitHub
  blob at the scanned commit, and include Mermaid diagrams where the
  model judged one useful.

Indexing is on demand: nothing is indexed until you press **Index
wiki** (or **Re-index** / **Rebuild** on a repo that already has one).
Each build runs in its own container from `wiki_image/` (the same
approach code sessions use), so the repo is cloned and scanned in
isolation. The container shallow-clones the repo, runs the
deterministic scan (`app/wiki_scan.py`), writes each applicable page
through the local model, and leaves its result in `/data/wiki.json`.
The API follows its progress from the container logs and only replaces
the stored wiki once the container has produced every page. A re-index
at a commit that's already been built is a no-op; **Rebuild** forces
it.

The build image is built on API startup, tagged
`FACTORY_WIKI_IMAGE_TAG` (default `factory-wiki:latest`). The app
package is mounted into the container at run time, so Python changes
don't need an image rebuild. Build containers are labelled
`factory.wiki=1`, and any left from a previous API process are removed
at startup. Note that `uvicorn --reload` restarts the API when Python
files change, which abandons a build in progress; the build container
keeps running, but nothing collects its result.

Everything is stored in `data/factory.db`: `wiki_indexes` (one row per
repo: status, progress, commit, the scan, and when the wiki was
created, last built and last changed) and `wiki_pages` (one row per
page). A build that's interrupted by an API restart is marked failed
on the next start.

## Models

The model server can host several models. Bertha reads its model list
(`/v1/models`, minus embedding models) into the **Models** tab. Each model
gets a tier from its parameter count: up to 8B is `light`, up to 14B
`standard`, larger is `strong`. You can change the tier, add a description,
or disable a model. Refresh after loading or removing models in the server.

A session is created on a model: pick one on the New session page, or it
gets the default (`FACTORY_DEFAULT_MODEL` if set, otherwise the strongest
enabled model). Code sessions write every enabled model into OpenCode's
config, but each turn runs on the session's model; the agent can't switch
models itself. Ask sessions call the chosen model directly.

Choosing a model per task automatically is not built yet; a session's model
is picked by you when it's created.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_TOKEN, GITHUB_BOT_USERNAME; FACTORY_REPOS is optional
```

`FACTORY_REPOS` seeds the repo registry at startup -- for now that's
the only way to register one (kept deliberately simple: no "add repo"
control in the UI yet, `POST /api/repos` exists if you want to script
it). Add the bot account as a collaborator on any repo you list there
(or give it org access) — it needs to be assignable to issues and
able to push branches / open PRs.

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

In a second terminal, the UI:

```bash
cd web
npm install
cp .env.local.example .env.local   # only needed if you changed FACTORY_HOST/PORT
npm run dev
```

Open `http://localhost:3000`.

## Repo layout

```
app/
  config.py         env settings
  store.py          sqlite: sessions, session_logs, repos, wiki tables
  github_client.py  polling: assigned issues, new PR comments; repo metadata
  docker_runner.py  per-turn ephemeral containers on a per-session volume
  scheduler.py      the state machine (ask + code, stop support)
  wiki_scan.py      deterministic codebase scan (languages, frameworks, docs, ...)
  wiki_build.py     clone, scan and page writing; runs inside the wiki container
  wiki_worker.py    the wiki container's entrypoint
  wiki_indexer.py   host side: starts a build container, follows it, stores the result
  main.py           FastAPI routes + CORS for the web/ origin
agent_image/
  Dockerfile        node+python+git+gh+opencode base image
  entrypoint.sh      clone/resume, run opencode, push, gh pr create
wiki_image/
  Dockerfile        python + git, for wiki build containers
web/                Next.js/TypeScript UI, its own process (npm run dev)
  app/              layout, globals.css, the one page (Sessions / Repos tabs)
  components/       SessionList, ComposeDraft, SessionDetail, ReposView,
                    RepoDetail, RepoMetadataPanel, RepoWiki, Markdown, MermaidDiagram
  lib/               api.ts (typed fetch client), types.ts, format.ts
```
