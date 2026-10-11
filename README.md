<p align="center">
  <img src="docs/bertha-logo.svg" alt="Bertha logo: a sleepy factory machine" width="96" height="96">
</p>

<h1 align="center">Bertha</h1>

<p align="center"><em>She builds...</em></p>

Bertha is a coding agent that works through your GitHub backlog using AI models
running on your own machine. Assign her an issue and she writes the code, opens
a pull request, and answers your review comments, all without sending your code
to a hosted AI service.

## Before you use it

Bertha is a **personal project**, built for my own use. Please read these
caveats before running it:

- **Not hardened against prompt injection.** Bertha hands issue text, pull
  request comments, code and CI logs to models that then act on them. Anyone
  who can write any of those can try to steer the agent. The containment
  below limits what a steered agent can do, but treat it as damage control,
  not as a security boundary.
- **Run it locally only.** The API and the web app have no authentication.
  Keep them on `localhost` (the default) and never expose them to a network or
  the internet.
- **Private repos with trusted people only.** Point Bertha only at private
  repos where everyone who can open issues, comment or push is someone you
  trust. Don't use it on public repos or ones with outside contributors.
- **Give it as little as possible.** Use a dedicated GitHub account with a
  fine-grained token limited to those repos, and protect each default branch
  so nothing merges without your review.
- **No guarantees.** It's early, changes often, and comes with no support.

## What it does

- **Turns issues into pull requests.** Assign an issue to Bertha's GitHub
  account, or describe a task in her web app, and she does the work in a
  throwaway container and opens a PR.
- **Responds to review.** Comment on her PR and she picks up the feedback and
  pushes another round. If CI fails, she reads the logs and fixes it.
- **Writes a plan first, when you want one.** For bigger work she can write a
  spec for you to approve on a draft PR before writing any code.
- **Answers questions.** Ask sessions are a plain chat with your local model.
- **Runs on a schedule.** For example, groom the backlog every night: tidy
  labels, flesh out thin issues, and close duplicates.
- **Learns your codebase.** She builds a wiki for each repo and keeps notes
  from your code reviews and her CI fixes. Agents read them on demand, and you
  decide which notes they get.

## Why it's useful

- **Private and free to run.** The models run locally (LM Studio or any
  OpenAI-compatible server), so your code stays on your machine and there's no
  per-token bill. When you want more, add a hosted provider (Anthropic, OpenAI,
  OpenRouter, DeepSeek, Gemini, or any OpenAI-compatible API) and routing uses
  the cheapest model that fits each task.
- **Contained by design.** Each task runs in an isolated container with no
  GitHub credentials and no open internet access. Bertha only ever *proposes*
  changes as pull requests; merging is always up to you. (This limits damage;
  it doesn't make Bertha safe for untrusted input. See the caveats above.)
- **Fits how you already work.** Issues, pull requests, review comments and CI:
  you steer Bertha from GitHub, or from her web app.

## Getting started

You'll need:

- **Docker**, running
- **Python 3.11+** and **Node.js 20+**
- **A local model server** with an OpenAI-compatible API, such as
  [LM Studio](https://lmstudio.ai), with a coding model loaded
- **A GitHub account for Bertha**, with a
  [fine-grained token](https://github.com/settings/personal-access-tokens)
  for the repos she'll work on: *Contents*, *Issues* and *Pull requests* read
  and write, and *Actions* read

Then:

```bash
# 1. Install and configure
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # set GITHUB_TOKEN, GITHUB_BOT_USERNAME, FACTORY_REPOS and your model

# 2. Start the service (the first start builds the agent's Docker image)
uvicorn app.main:app --reload --reload-dir app --port 8787

# 3. In a second terminal, start the web app
cd web && npm install && npm run dev
```

Open <http://localhost:3000>, start a session, or assign an issue to Bertha's
account and watch it appear.

Add Bertha's account as a collaborator on each repo in `FACTORY_REPOS`, and
protect your default branch so her pull requests need a review.

## Learn more

[How Bertha works](docs/how-it-works.md) covers the details: sessions, the
containment model, GitHub tools, models and routing, the wiki and agent
context, schedules, and the code layout.

Bertha is a single-user personal project meant to run on your own machine,
against your own private repos. See [Before you use it](#before-you-use-it).
