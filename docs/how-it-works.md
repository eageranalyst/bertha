# How Bertha works

The full picture, for people running or changing Bertha. For what it is and how
to get started, see the [README](../README.md).

Bertha is a lightweight local service that picks up GitHub issues assigned to a
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

## Agent permissions

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
surface -- loosen either in `images/agent/entrypoint.sh` if a repo
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
     seen, and queues a new turn with those comments as the prompt. Only
     comments from people who can write to the repo count. Bertha's own
     comments carry a hidden `<!-- bertha -->` marker and are skipped, so
     it never wakes itself, even when it posts as your account
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
  on its own port, talking to the FastAPI backend over CORS (`app/main.py`
  allows `localhost:3000`). The session list polls `/api/sessions` every
  few seconds; an open session follows `/api/sessions/{id}/events`, a
  server-sent event stream of its status and log changes, so ask replies
  appear as the model writes them and code-turn output as the scheduler
  copies it from the container (every `FACTORY_FAST_POLL_INTERVAL_SECONDS`). Model
  responses and user messages render as Markdown with syntax
  highlighting (`react-markdown` + `rehype-highlight`); raw container
  logs (`agent_output`) render as plain preformatted text instead,
  since that's an NDJSON dump, not prose.

## Spec-first sessions

For bigger or vaguer work, a code session can write a spec for you to
review before any code (`app/specs.py`). Tick **Spec first** when starting
a session (it's on by default for long prompts), or label an issue
`needs-spec` before assigning it to the bot.

1. **Spec.** The first turn reads the code and writes
   `specs/<issue-or-ref>-<title>.md`, then opens a **draft** PR, with the
   task's title, labelled `spec-review`. A spec turn may only write the spec:
   the entrypoint throws away any other change and says so in the session.
2. **Review.** Comment on the PR, on lines of the spec or in the
   conversation. Each batch of comments starts a revise turn that updates the
   spec and replies on the PR. Messages sent in Bertha count as feedback too.
3. **Approve.** Comment `/approve-spec` (or press **Approve and build** in the
   session). Any comments sent with the approval go to the build too. The build
   turn starts a fresh agent conversation with the approved spec as its plan,
   on the same branch. When it finishes, the PR is marked ready for review,
   with the spec and the code together.
4. `/abandon` (or **Abandon**) closes the PR and the session.

The PR's label (`spec-review`, then `building`) and a status line at the top
of its description show where it is. Only comments from people with write
access steer a session; others are noted in the session and otherwise
ignored. Comments wait while a turn runs, and are picked up after it.

## Repos and wiki

The **Repos** tab lists every registered repo. Each one has these
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
- **Skills** -- which skills from the library agents get in this repo.
- **Context** -- lessons for agents drawn from the repo's reviews and CI
  fixes, for you to review (see Agent context).

Indexing starts on demand: nothing is indexed until you press **Index
wiki** (or **Re-index** / **Rebuild** on a repo that already has one).
After that, Bertha keeps it fresh: every 10 minutes it checks each wiki's
repo for pull requests merged into the default branch, and a merge marks the
wiki out of date (shown on the Wiki tab, and to agents). An out-of-date wiki
is rebuilt once its last build is older than **Wiki refresh (days)** in
Settings (7 by default).
Each build runs in its own container from `images/wiki/` (the same
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

## Agent context

Each repo has a **Context** tab (Repos → the repo → Context) that collects
lessons for agents from what happens in its sessions (`app/agent_context.py`):

- **Review notes.** When a Bertha pull request is merged or closed, the
  local model reads its comments from people with write access and keeps the
  rules that apply beyond that change ("go through the repository functions,
  don't hand-roll SQL"), dropping typos, questions and thanks.
- **CI notes.** When CI passes after failing on a session's pull request,
  the model writes up the cause and the fix from the failure and the change
  between the two commits, or skips it if it looks flaky or one-off.

Each note keeps where it came from (the session, the PR, links to the
comments, the CI jobs and the fix's diff) and the words it was drawn from.
New notes wait in **To review**: save one to memory, reject it, edit it, or
vote it up or down. Only saved notes reach agents: their titles in each
turn's repo context, highest-voted first, and the full note through the
`context_note` tool, which counts how often agents read each one.

## Schedules

The **Schedules** tab runs a prompt on a cron schedule. Each run starts a
normal session (it shows in the Sessions list, and in the schedule's run
history), with the schedule's repo, optional model, and session type.

- The cron is entered and shown in your local time zone; it's stored with
  that zone, so "02:00 nightly" stays at 02:00 across daylight-saving
  changes, and every timestamp is stored in UTC (`app/cron.py`).
- Each schedule has a **timeout**: a run past it is stopped. A run that
  comes due while the previous run is still going is recorded as failed and
  doesn't start, so runs never overlap.
- A run missed while Bertha was off happens once when it starts again, not
  once per missed slot.
- **Run now** starts a run immediately, under the same rules.

The **Backlog groom** template grooms a repo's open issues: it follows the
repo's own grooming rules (a `groom-backlog` skill in `.claude/skills/`, and
the label vocabulary in its README, if present), fleshes out thin issues,
applies the standard labels, closes duplicates and stale issues, and asks
questions in comments.

### GitHub tools

Agents have no GitHub token. Every session with a repo gets Bertha's GitHub
tool server instead (`app/github_tools.py`), an MCP server that holds the
token. Each turn gets its own key, scoped to the session's repo and revoked
when the turn ends. What the key allows:

| | Every repo session | With **GitHub tools** on (e.g. backlog groom) |
|---|---|---|
| Read any issue, label, PR, CI check and CI log | ✅ | ✅ |
| Edit and comment on its **own issue** (the one it was started from) | ✅ | ✅ |
| Edit, comment on and close any issue | | ✅ |
| Open, edit and comment on its **own PR**; push its branch mid-turn | ✅ | ✅ |

Dry runs only read. "Own" comes from the session record, not from the agent.
Pushes run in a separate short-lived container (`images/agent/push.sh`), so
the agent's container never holds the token: the agent's changes are
bundled without the token and pushed from a fresh repository, to the
session's branch only and never forced. Bertha still commits, pushes and
opens the PR at the end of every turn, so the tools are optional. A spec
under review can't be pushed or marked ready by the agent.

Changes apply directly, so there are guardrails: only the session's repo, no
deletes or merges, only labels the repo already has, and per-turn limits on
edits, closures and pushes (Settings). Every change except a push is
recorded with the values before it, and the session's **GitHub changes**
panel can undo a run's changes.

Linking an issue: give an issue number when starting a session, or name one
plainly in the prompt ("issue 42", an issue URL, or `#42` in a short
prompt). The issue's text goes with the task, the PR closes it, and it
becomes the session's own issue.

**CI.** After a turn pushes to its PR, Bertha watches the PR's CI. When it
fails, it starts a turn with the failing jobs' trimmed logs, at most twice in
a row; after that the session is flagged for you. Sending a message, or CI
passing, resets the count. A spec-first PR is marked ready for review once
CI passes.

The bot's token needs **Issues: read and write**, **Pull requests: read and
write**, **Contents: read and write** and **Actions: read** on repos where
this is used. Without Actions: read, Bertha can't see CI, and says so in the
session.

**Git with the token.** The agent can write its whole workspace, including
`.git` and `$HOME`, so the startup script never runs git with the token under
anything the agent wrote. It stops the agent's leftover processes, uses a
global git and `gh` config outside the workspace, and rewrites the repo's
`.git/config` with hooks off before committing and pushing
(`images/agent/git_safe.sh`).

## Agent performance

What the agent gets, besides the task:

- **Working practices** (`images/agent/WORKING.md`) and **repo context**
  (`app/agent_context.py`). The context only says what exists, so it stays
  small however big the wiki gets: the wiki's commit and freshness, a short
  summary, one line per page, and the titles of saved notes (see Agent
  context below). The agent reads the rest with Bertha's context tools
  (`app/context_tools.py`, an MCP server called `bertha` beside the GitHub
  tools, with the same per-turn key): `wiki_pages`, `wiki_page`,
  `wiki_search`, `repo_map`, `search_code`, `context_notes` and
  `context_note`. All are read-only and limited to the session's repo.
- **Likely relevant code**: the wiki build also indexes the repo's code
  (`app/code_index.py`), embedding chunks with the model server's embedding
  model (`FACTORY_EMBEDDING_MODEL`, default `nomic-embed-text`). Each turn,
  the task is matched against it, by meaning and by file and symbol names,
  and the best matches go in the first message. An existing wiki gets its
  index on the next Re-index without rewriting its pages.
- **Dependencies installed** once per workspace (npm lockfiles, Python
  requirements), so the agent can run tests.

Keeping it fast: OpenCode compacts its conversation at the **Working
context** setting (48k by default) rather than the model's full context,
and prunes old tool output. Tool output is capped per call.

In LM Studio, mixture-of-experts models (like Qwen 35B-A3B) are much faster
per token than dense ones of similar quality. Load models with the context
you need rather than the maximum, and turn on flash attention and KV-cache
quantization if your version has them; both cut memory and speed up long
prompts.

### Evals

`evals/run.py` runs the agent on the tasks in `evals/tasks.json` as dry-run
sessions (they commit in the workspace but never push or open a PR), checks
the results, and writes them to `evals/results/`. Run it before and after a
change to see whether it helped. Tasks name your own repos, so
`evals/tasks.json` isn't committed: copy `evals/tasks.example.json` to start
yours.

```bash
python evals/run.py                          # every task, with routing
python evals/run.py --model qwen3.6-35b-a3b  # pin a model
```

Add tasks from real sessions: what was asked, and a check that only passes
when it was done (a file changed, a reply mentions the right module, or a
test command passes).

## Containment

Agents run code from the repos they work on, so the boundary around them
does most of the work, the way hosted agents do it:

- **Network.** Agent and wiki containers run on an internal Docker network
  (`factory-agents`) with no route to this machine or the internet. Their
  only exit is the `factory-egress` proxy (`app/egress_proxy.py`), which
  forwards to the model server and allows HTTPS to GitHub and the npm and
  PyPI registries. Bertha's own API is unreachable from a container. Add
  hosts with `FACTORY_EGRESS_ALLOW`; `FACTORY_EGRESS=open` turns isolation
  off.
- **Credentials.** The agent runs as `node`, without `GITHUB_TOKEN` in its
  environment. Cloning, pushing and opening the PR happen in the entrypoint,
  outside the agent. A repo's own `opencode.json` is ignored, so it can't
  loosen the agent's permissions.
- **Resources.** Each container gets memory and CPU limits (Settings) and a
  process cap.
- **GitHub.** Use a fine-grained token limited to the registered repos
  (Contents and Pull requests: read and write), and protect each repo's
  default branch so agent PRs need review. Settings → Security shows both.

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

**Routing.** A session on the default ("Auto") is routed turn by turn
(`app/routing.py`). Before each turn, the lightest enabled model reads the
task and says whether it needs light, standard or strong; the turn runs on an
enabled model in that tier, and the session's model is updated and logged
with the reason. A turn that failed moves the next one up a tier. Routing
only changes the model when the tier changes, to avoid reloads. If the
classifier can't answer, the turn keeps the current model.

Picking a model yourself (on the New session page, or in a session's Model
selector) pins the session to it, and routing leaves it alone. "Auto" hands
it back. `FACTORY_ROUTING=0` turns routing off everywhere.

The classifier runs on the host and is synchronous, so each routed turn waits
for one short model call, up to 20 seconds.

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
from `images/agent/` and the wiki build image from `images/wiki/`.

```bash
uvicorn app.main:app --reload --reload-dir app --port 8787
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
  preflight.py      checks run before the server starts
  store.py          sqlite: sessions, session_logs, repos, schedules, wiki tables
  scheduler.py      the state machine (ask + code, stop support)
  docker_runner.py  per-turn ephemeral containers on a per-session volume
  egress_proxy.py   the agent network's only way out
  github_client.py  polling: assigned issues, new PR comments; repo metadata
  github_tools.py   MCP server giving agents scoped GitHub access without a token
  context_tools.py  MCP server giving agents the wiki, code map, code search and saved notes
  agent_context.py  repo context for a turn; notes drawn from reviews and CI fixes
  specs.py          spec-first sessions
  models.py         model registry: discovery, tiers, choosing a model
  routing.py        per-turn model routing
  agent_settings.py limits on code turns (Settings)
  cron.py           cron expressions for schedules
  skills.py         SKILL.md parsing for the skills library
  code_index.py     embedding-based code search for agents
  wiki_scan.py      deterministic codebase scan (languages, frameworks, docs, ...)
  wiki_build.py     clone, scan and page writing; runs inside the wiki container
  wiki_worker.py    the wiki container's entrypoint
  wiki_indexer.py   host side: starts a build container, follows it, stores the result
  main.py           FastAPI routes + CORS for the web/ origin
images/             Docker image build folders
  agent/            node+python+git+gh+opencode image for code sessions:
                    entrypoint.sh, push.sh, git_safe.sh, WORKING.md, run_summary.py
  wiki/             python + git, for wiki build containers
evals/              agent evals (run.py, tasks.example.json)
docs/               this guide, and the logo
web/                Next.js/TypeScript UI, its own process (npm run dev)
  app/icon.svg      favicon: a simplified Bertha logo
  app/(console)/    one route per tab: sessions, repos (metadata, wiki,
                    skills, context), schedules, skills, models, settings
  components/       UI components; images/ holds the SVG mascots and logo
  lib/              api.ts (typed fetch client), types.ts, format.ts, ...
```
