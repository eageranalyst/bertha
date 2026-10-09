#!/usr/bin/env bash
# One turn of work: resume (or start) an OpenCode session at /data/repo,
# push whatever changed, and open a PR if one doesn't exist yet --
# all of that only if REPO is set. A "code" session isn't required to
# have a repo attached; without one this just runs the agent against a
# plain local workspace with no git remote, no push, no PR. Everything
# under /data is on a volume that outlives this container, so the next
# turn (a wake-up, or a repo getting attached later) just reruns this
# same script against the same volume.
#
# Permission posture: the agent works like a hosted agent in its sandbox.
# Bash is allowed by default, because the boundary does the security work:
# the container has no route out except the egress proxy (GitHub, package
# registries, the model server), the agent runs as an unprivileged user,
# and its environment holds no credentials (see AS_AGENT below). What's
# left on the denylist is what would wreck the turn or bypass Bertha:
# pushing (Bertha pushes after the turn), wiping the workspace, piping
# downloads into a shell, and disk tools.
#
# How the rules match, in this opencode build:
# - Each part of a pipeline or chain is checked on its own, and the LAST
#   matching rule wins, so the denies after "*" apply.
# - A denied command returns an error to the model, which carries on.
#   (An "ask" rule would end the whole run when nobody is there to answer.)
set -euo pipefail

REPO="${REPO:-}"
BRANCH_NAME="${BRANCH_NAME:-}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN env var required}"
: "${PROMPT:?PROMPT env var required}"
: "${OPENCODE_MODEL:?OPENCODE_MODEL env var required}"
: "${LOCAL_MODEL_PROVIDER:?LOCAL_MODEL_PROVIDER env var required}"
: "${LOCAL_MODEL_BASE_URL:?LOCAL_MODEL_BASE_URL env var required}"
: "${LOCAL_MODEL_KEY:?LOCAL_MODEL_KEY env var required}"
: "${LOCAL_MODEL_ID:?LOCAL_MODEL_ID env var required}"

export HOME=/data
export GH_TOKEN="$GITHUB_TOKEN"
mkdir -p "$HOME/.config/opencode"

# Skills enabled for this repo, written fresh every turn so a skill that
# was switched off since the last turn disappears. OpenCode reads them
# from $HOME/skills (see "skills" in the config below).
rm -rf "$HOME/skills"
mkdir -p "$HOME/skills"
printf '%s' "${SKILLS_JSON:-[]}" | python3 -c '
import json, os, pathlib, sys
root = pathlib.Path(os.environ["HOME"]) / "skills"
for skill in json.load(sys.stdin):
    folder = root / skill["name"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(skill["content"])
'

# Every enabled model Bertha knows about, so the agent can use the right one
# for the task. Falls back to the single model in LOCAL_MODEL_* when none
# were sent.
MODELS_BLOCK=$(printf '%s' "${MODELS_JSON:-[]}" | python3 -c '
import json, os, sys
models = json.load(sys.stdin) or [{"key": os.environ["LOCAL_MODEL_KEY"], "model_id": os.environ["LOCAL_MODEL_ID"]}]
def entry(m):
    e = {"id": m["model_id"], "name": m["model_id"]}
    if m.get("context"):
        # OpenCode compacts when this fills, so a working size keeps steps fast.
        e["limit"] = {"context": int(m["context"]), "output": 8192}
    return e
print(json.dumps({m["key"]: entry(m) for m in models}))
')

# Bertha's GitHub tools, when this session has them: an MCP server that holds
# the token itself, reached with a key that's only valid for this turn and
# this repo (app/github_tools.py). The agent sees tools, never a token.
# The same key opens Bertha's context tools (app/context_tools.py): the wiki,
# code map, code search and saved notes, which the standing context only lists.
MCP_BLOCK=""
if [ -n "${GITHUB_TOOLS_URL:-}" ] && [ -n "${GITHUB_TOOLS_KEY:-}" ]; then
    AUTH="\"headers\": { \"Authorization\": \"Bearer ${GITHUB_TOOLS_KEY}\" }"
    MCP_BLOCK="  \"mcp\": { \"github\": { \"type\": \"remote\", \"url\": \"${GITHUB_TOOLS_URL}\", \"enabled\": true, ${AUTH} }"
    if [ -n "${CONTEXT_TOOLS_URL:-}" ]; then
        MCP_BLOCK="${MCP_BLOCK}, \"bertha\": { \"type\": \"remote\", \"url\": \"${CONTEXT_TOOLS_URL}\", \"enabled\": true, ${AUTH} }"
    fi
    MCP_BLOCK="${MCP_BLOCK} },"
fi

# What Bertha knows about this repo (a summary of its wiki and the notes people
# saved, with the rest behind the context tools), loaded with the working
# practices as standing instructions, the way CLAUDE.md is in Claude Code.
mkdir -p "$HOME/factory"
printf '%s\n' "${REPO_CONTEXT:-}" > "$HOME/factory/repo-context.md"

cat > "$HOME/.config/opencode/opencode.jsonc" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "${LOCAL_MODEL_PROVIDER}": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local models (factory)",
      "options": { "baseURL": "${LOCAL_MODEL_BASE_URL}" },
      "models": ${MODELS_BLOCK}
    }
  },
  "skills": { "paths": ["${HOME}/skills", "/data/repo/.claude/skills"] },
${MCP_BLOCK}
  "agent": {
    "build": { "steps": ${AGENT_STEPS:-25} },
    "worker": {
      "mode": "subagent",
      "description": "Does one self-contained part of a larger task (one issue, one file, one module) in its own fresh context, then reports back briefly. Give it everything it needs in the prompt: the part to do, the rules to follow, and what to report.",
      "steps": ${AGENT_STEPS:-25},
      "prompt": "You are working on one part of a larger task, in a fresh context. Do that part completely, following the rules you were given and your working practices, using the tools you have. Don't start on other parts. When you're done, reply with a short report of two to five lines: what you did, what you changed (with names or numbers), and anything you couldn't do. The report is all the caller sees."
    }
  },
  "tool_output": { "max_lines": 200, "max_bytes": 8192 },
  "instructions": ["/opt/factory/${PERSONA_FILE:-PERSONA.md}", "/opt/factory/WORKING.md", "${HOME}/factory/repo-context.md"],
  "compaction": { "auto": true, "prune": true },
  "permission": {
    "edit": "allow",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
    "bash": {
      "*": "allow",
      "git push*": "deny",
      "git * push*": "deny",
      "*rm -rf /": "deny",
      "*rm -rf / *": "deny",
      "*rm -rf /data": "deny",
      "*rm -rf /data/": "deny",
      "*rm -rf /data/repo": "deny",
      "*rm -rf /data/repo/": "deny",
      "*rm -rf ~*": "deny",
      "*rm -rf \$HOME*": "deny",
      "*sudo *": "deny",
      "* dd *": "deny",
      "dd *": "deny",
      "*mkfs*": "deny",
      "*:(){ :": "deny",
      "*|sh*": "deny",
      "*| sh*": "deny",
      "*|bash*": "deny",
      "*| bash*": "deny"
    }
  }
}
EOF

# Every git command this script runs holds the token, so none of them may
# read config or hooks the agent wrote (see git_safe.sh).
source /opt/factory/git_safe.sh
write_global_git_config

cd /data
if [ -n "$REPO" ]; then
    if [ ! -d repo/.git ]; then
        echo "=== Cloning $REPO ==="
        git clone "https://github.com/${REPO}.git" repo
    fi
    reset_repo_git_config /data/repo
    cd repo
    if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
        git fetch -q origin "$BRANCH_NAME"
        if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
            # Keep work an earlier turn committed but couldn't push, and take
            # in anything pushed to the branch since (see git_safe.sh).
            git checkout -q "$BRANCH_NAME"
            sync_with_remote "$BRANCH_NAME" || true
        else
            git checkout -q -b "$BRANCH_NAME" "origin/$BRANCH_NAME"
        fi
    elif git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
        # Left over from an earlier turn that never reached the remote: keep
        # its commits rather than failing to create the branch again.
        git checkout "$BRANCH_NAME"
    else
        git checkout -b "$BRANCH_NAME"
    fi
else
    echo "=== No repo attached, working in a plain local workspace ==="
    mkdir -p repo
    cd repo
    if [ ! -d .git ]; then
        git init -q
        git commit -q --allow-empty -m "factory: workspace init"
    fi
fi

# A review's scratch space for throwaway tests: inside the checkout, because
# the agent can't touch files outside it, and ignored by git. A review
# session never pushes, so none of it leaves the container.
if [ -n "$REPO" ] && [ -n "${REVIEW_OF_PR:-}" ]; then
    mkdir -p /data/repo/.qc
    grep -qx '.qc/' /data/repo/.git/info/exclude 2>/dev/null || echo '.qc/' >> /data/repo/.git/info/exclude
fi

# A repo turn is one shot: nobody answers questions while the agent works,
# so the instructions say to finish the task in this run and state what was
# done. The final message is reused as the pull request description.
MESSAGE="$PROMPT"
if [ -n "$REPO" ] && [ "${SPEC_MODE:-}" = "draft" ]; then
    # A spec turn: Bertha wrote the whole message (app/specs.py). Only the
    # spec file is kept afterwards, so the turn can't slip into building.
    : "${SPEC_PATH:?SPEC_PATH env var required for a spec turn}"
elif [ -n "$REPO" ] && [ -n "${REVIEW_OF_PR:-}" ]; then
    # A code review (QC): someone else's change, checked out read-only. Bertha
    # wrote the task; nothing this turn changes is pushed (NO_PUSH).
    MESSAGE="You are reviewing pull request #$REVIEW_OF_PR, checked out in /data/repo on its branch $BRANCH_NAME. Review it with the bertha-review skill: load it with the skill tool and follow every step. Don't change the repository's files; throwaway tests to prove a finding go in /data/repo/.qc/, which git ignores. You inspect, and Bertha fixes what you find.

$PROMPT"
elif [ -n "$REPO" ]; then
    # The general working practices come from WORKING.md (see "instructions"
    # in the config); this only adds what's specific to this turn.
    MESSAGE="You are working in the git checkout at /data/repo, on branch $BRANCH_NAME. Carry the task through to finished, verified work in this one run, following your working practices. Your changes are committed and proposed as a pull request when you finish. If the task needs no code change, answer it and say so.

Task:
$PROMPT"
    if [ -n "${RELEVANT_CODE:-}" ]; then
        MESSAGE="$MESSAGE

## Likely relevant code

A search of the repository found these for the task. It can be wrong or
out of date, so check it, but start here before searching.

$RELEVANT_CODE"
    fi
fi

# Where the turn started, so a spec turn's other changes can be thrown away.
START_SHA=$(git rev-parse HEAD)

# The agent runs as the unprivileged "node" user, with the GitHub token
# removed from its environment. It never needs the token: this script does
# the clone, push and PR as root, outside the agent. As a different user it
# also can't read this script's environment through /proc.
chown -R node:node /data
AS_AGENT=(setpriv --reuid=node --regid=node --init-groups env -u GITHUB_TOKEN -u GH_TOKEN)

# OpenCode settings for an unattended, network-restricted run:
# - project config off: a cloned repo's own opencode.json must not be able to
#   loosen the permissions above;
# - no model catalogue fetch, update check or language-server download: the
#   egress proxy refuses them, and waiting on them makes each run slower;
# - no session sharing.
export OPENCODE_DISABLE_PROJECT_CONFIG=1
export OPENCODE_DISABLE_MODELS_FETCH=1
export OPENCODE_DISABLE_AUTOUPDATE=1
export OPENCODE_DISABLE_LSP_DOWNLOAD=1
export OPENCODE_DISABLE_SHARE=1

# Dependencies, so the agent can run the project's tests and build. Installed
# once per workspace and again only when a lockfile or requirements file
# changes. They run as the agent user without the token: install scripts are
# code from the repo too. A failed install is reported, not fatal.
install_dependencies() {
    cd /data/repo
    local manifests key
    manifests=$(ls package-lock.json ./*/package-lock.json requirements.txt ./*/requirements.txt 2>/dev/null || true)
    [ -z "$manifests" ] && return 0
    key=$(cat $manifests | sha256sum | cut -c1-16)
    if [ "$(cat /data/.deps-key 2>/dev/null)" = "$key" ]; then
        return 0
    fi
    for lock in $(ls package-lock.json ./*/package-lock.json 2>/dev/null || true); do
        dir=$(dirname "$lock")
        echo "=== Installing npm dependencies in ${dir} ==="
        (cd "/data/repo/$dir" && timeout 900 "${AS_AGENT[@]}" npm ci --no-audit --no-fund --loglevel=error) \
            || echo "FACTORY_DEPS_FAILED=npm ci in ${dir}"
    done
    for req in $(ls requirements.txt ./*/requirements.txt 2>/dev/null || true); do
        echo "=== Installing Python dependencies from ${req} ==="
        [ -x /data/venv/bin/pip ] || "${AS_AGENT[@]}" python3 -m venv /data/venv
        timeout 900 "${AS_AGENT[@]}" /data/venv/bin/pip install --quiet -r "/data/repo/$req" \
            || echo "FACTORY_DEPS_FAILED=pip install -r ${req}"
    done
    echo "$key" > /data/.deps-key
}
if [ -n "$REPO" ]; then
    install_dependencies
    cd /data/repo
fi
# The agent finds the workspace's Python environment first, if there is one.
export PATH="/data/venv/bin:$PATH"

# A turn runs the agent until it answers. A run can end early (a rejected
# command stops OpenCode), so it is resumed in the same session, within the
# limits from the Settings screen: continuations, the token budget, a
# wall-clock timeout per run, and a stop if the same command is rejected twice.
# A run that ends with an answer ends the turn.
RESULT=/data/last_result.ndjson
RUN_FILE=/data/run.ndjson
: > "$RESULT"
SESSION="${OPENCODE_SESSION_ID:-}"
MESSAGE_THIS_RUN="$MESSAGE"
CONTINUATIONS_USED=0
TOKENS_USED=0
PREVIOUS_REJECTED=""
PREVIOUS_LOOP=""
STATE="unfinished"
AGENT_EXIT=0
while true; do
    echo "=== Running agent (session ${SESSION:-<new>}, model $OPENCODE_MODEL, run $((CONTINUATIONS_USED + 1))) ==="
    : > "$RUN_FILE"
    # The events go to stdout as they happen (Bertha streams the container's
    # output into the transcript) and to the run file for the summary below.
    # The watcher also stops the run when the agent keeps failing on the same
    # thing (the same missing file, say), rather than letting it spend the
    # run's steps retrying.
    set +e
    if [ -n "$SESSION" ]; then
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" "${AS_AGENT[@]}" opencode run "$MESSAGE_THIS_RUN" \
            -s "$SESSION" -m "$OPENCODE_MODEL" --format json | python3 /opt/factory/run_summary.py --watch "$RUN_FILE"
    else
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" "${AS_AGENT[@]}" opencode run "$MESSAGE_THIS_RUN" \
            -m "$OPENCODE_MODEL" --format json | python3 /opt/factory/run_summary.py --watch "$RUN_FILE"
    fi
    AGENT_EXIT=${PIPESTATUS[0]}
    set -e
    cat "$RUN_FILE" >> "$RESULT"

    FOUND_SESSION=$(grep -m1 -o '"sessionID":"[^"]*"' "$RUN_FILE" | head -1 | cut -d'"' -f4 || true)
    if [ -n "$FOUND_SESSION" ]; then
        SESSION="$FOUND_SESSION"
    fi
    SUMMARY=$(python3 /opt/factory/run_summary.py "$RUN_FILE")
    STATE=$(printf '%s\n' "$SUMMARY" | sed -n 's/^STATE=//p')
    TOKENS=$(printf '%s\n' "$SUMMARY" | sed -n 's/^TOKENS=//p')
    REJECTED=$(printf '%s\n' "$SUMMARY" | sed -n 's/^REJECTED=//p')
    LOOP=$(printf '%s\n' "$SUMMARY" | sed -n 's/^LOOP=//p')
    STEP_LIMIT=$(printf '%s\n' "$SUMMARY" | sed -n 's/^STEP_LIMIT=//p')
    TOKENS_USED=$((TOKENS_USED + ${TOKENS:-0}))
    echo "=== Run finished (exit $AGENT_EXIT, $STATE, ${TOKENS:-0} tokens; turn total $TOKENS_USED) ==="

    if [ "$AGENT_EXIT" -eq 124 ]; then
        echo "FACTORY_RUN_TIMED_OUT=1"
        break
    fi
    if [ -n "$LOOP" ]; then
        echo "FACTORY_LOOP=$LOOP"
        STATE="unfinished"  # cut off mid-run, whatever its last step said
    fi
    if [ -n "$STEP_LIMIT" ] && [ "$STATE" = "done" ]; then
        # Out of steps, the model is made to answer, which looks like done.
        echo "FACTORY_STEP_LIMIT=1"
        STATE="unfinished"
    fi
    if [ "$STATE" = "done" ]; then
        break
    fi
    if [ "$STATE" = "error" ]; then
        # The model rejected the request itself; the same model will again.
        echo "FACTORY_MODEL_ERROR=$(printf '%s\n' "$SUMMARY" | sed -n 's/^ERROR=//p')"
        if [ -n "$(printf '%s\n' "$SUMMARY" | sed -n 's/^CONTEXT=//p')" ]; then
            echo "FACTORY_CONTEXT_TOO_SMALL=1"
        fi
        if [ -n "$(printf '%s\n' "$SUMMARY" | sed -n 's/^UNLOADABLE=//p')" ]; then
            echo "FACTORY_MODEL_UNAVAILABLE=1"
        fi
        break
    fi
    if [ -z "$SESSION" ]; then
        echo "FACTORY_CONTINUE_STOPPED=the run has no session to resume"
        break
    fi
    if [ "$CONTINUATIONS_USED" -ge "${AGENT_CONTINUATIONS:-2}" ]; then
        echo "FACTORY_CONTINUE_STOPPED=continuation limit reached"
        break
    fi
    if [ "$TOKENS_USED" -ge "${AGENT_TOKEN_BUDGET:-150000}" ]; then
        echo "FACTORY_CONTINUE_STOPPED=token budget used"
        break
    fi
    if [ -n "$LOOP" ] && [ "$LOOP" = "$PREVIOUS_LOOP" ]; then
        echo "FACTORY_CONTINUE_STOPPED=the agent kept retrying the same failing thing after being told"
        break
    fi
    if [ -z "$LOOP" ] && [ -n "$REJECTED" ] && [ "$REJECTED" = "$PREVIOUS_REJECTED" ]; then
        echo "FACTORY_CONTINUE_STOPPED=the same command was rejected again"
        break
    fi
    PREVIOUS_REJECTED="$REJECTED"
    PREVIOUS_LOOP="$LOOP"
    CONTINUATIONS_USED=$((CONTINUATIONS_USED + 1))
    if [ -n "$STEP_LIMIT" ] && [ -z "$LOOP" ]; then
        MESSAGE_THIS_RUN="Your last run used all its steps before the task was finished. You have more now: carry on from where you got to, and finish with a final answer once the work is done and checked."
    elif [ -n "$LOOP" ]; then
        MESSAGE_THIS_RUN="Bertha stopped your last run because you were going round in circles: $LOOP. Don't do it again. If something keeps failing on a path, list its parent directory (or use glob) and use the name that's actually there. If a command keeps giving the same result, it won't change: check its output, decide, and move on. Then carry on with the task and finish with a final answer."
    else
        MESSAGE_THIS_RUN="The previous run stopped before you answered, usually because a command was rejected. Carry on with the task: use the glob, grep and read tools, or a simpler command, and finish with a final answer."
    fi
done

# A turn that never reached an answer is a failure, not a clean finish.
if [ "$STATE" != "done" ] && [ "$AGENT_EXIT" -eq 0 ]; then
    AGENT_EXIT=1
fi
echo "=== Agent finished (exit $AGENT_EXIT) ==="

if [ -n "$SESSION" ]; then
    echo "FACTORY_OPENCODE_SESSION_ID=${SESSION}"
fi

if [ -n "$REPO" ]; then
    # The agent has had the checkout to itself. Before running git with the
    # token, stop anything it left running and take the git config back
    # (see git_safe.sh).
    pkill -KILL -u node 2>/dev/null || true
    reset_repo_git_config /data/repo
    # The agent's final report: the commit body and the pull request description.
    python3 - > /data/pr_summary.md <<'PY' || true
import json
last_message = ""
try:
    with open("/data/last_result.ndjson") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            part = event.get("part") or {}
            if event.get("type") == "text" and (part.get("text") or "").strip():
                last_message = part["text"]
except OSError:
    pass
print(last_message.strip()[-4000:])
PY

    if [ "${SPEC_MODE:-}" = "draft" ]; then
        # Keep the spec, drop everything else the agent changed or committed.
        DISCARDED=$( { git diff --name-only "$START_SHA"; git ls-files --others --exclude-standard; } \
            | grep -vxF "$SPEC_PATH" | sort -u | head -20 | tr '\n' ' ' || true)  # none is the usual case
        SPEC_TMP=$(mktemp)
        if [ -f "$SPEC_PATH" ]; then cp "$SPEC_PATH" "$SPEC_TMP"; fi
        git reset -q --hard "$START_SHA"
        git clean -qfd
        if [ -s "$SPEC_TMP" ]; then
            mkdir -p "$(dirname "$SPEC_PATH")"
            cp "$SPEC_TMP" "$SPEC_PATH"
        else
            echo "FACTORY_SPEC_MISSING=1"
            AGENT_EXIT=1
        fi
        if [ -n "$DISCARDED" ]; then
            echo "FACTORY_SPEC_DISCARDED=$DISCARDED"
        fi
    fi

    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        {
            echo "${COMMIT_TITLE:-${PR_TITLE:-agent: changes for $BRANCH_NAME}}"
            if [ -s /data/pr_summary.md ]; then echo; cat /data/pr_summary.md; fi
        } > /data/commit_message.txt
        git commit -F /data/commit_message.txt >/dev/null
    fi

    # Workflow files are never pushed: a workflow an agent wrote would run in
    # CI with the repo's secrets before anyone reviewed it (and the token
    # isn't allowed to change them). The commits not yet on GitHub are
    # squashed into one without them, and the change itself is handed to
    # Bertha, which posts it on the pull request for a person to apply.
    if [ -z "${NO_PUSH:-}" ]; then
        # GitHub's copy as it is now: a mid-turn push, or someone else, may
        # have moved it. Squashing must never touch what's already there.
        if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
            git fetch -q origin "$BRANCH_NAME"
            sync_with_remote "$BRANCH_NAME" || true
        fi
        hold_back_workflows "$BRANCH_NAME"
    fi

    # Commits this branch has beyond the default branch. Zero means the
    # agent changed nothing, which is a normal outcome, not a failure.
    AHEAD=$(git rev-list --count "origin/HEAD..HEAD" 2>/dev/null || echo 0)
    PUSH_FAILED=""
    if [ "$AHEAD" -eq 0 ]; then
        echo "FACTORY_NO_CHANGES=1"
    elif [ -n "${NO_PUSH:-}" ]; then
        # An eval run: the commit stays in the workspace for checking.
        echo "FACTORY_DRY_RUN=1"
    elif git push -q origin "$BRANCH_NAME" 2>/data/push_error.txt; then
        echo "FACTORY_PUSHED=1"
    else
        # Reported, not fatal: the work stays committed here for the next turn.
        cat /data/push_error.txt
        echo "FACTORY_PUSH_FAILED=$(grep -E 'rejected|refusing|error|fatal' /data/push_error.txt | head -3 | tr '\n' ' ')"
        PUSH_FAILED=1
        AGENT_EXIT=1
    fi

    if [ -z "${NO_PUSH:-}" ] && [ -z "$PUSH_FAILED" ] && [ "$AHEAD" -gt 0 ] && { [ -z "${PR_NUMBER:-}" ] || [ "${PR_NUMBER}" = "0" ]; }; then
        {
            if [ -s /data/pr_summary.md ]; then cat /data/pr_summary.md; echo; fi
            echo "${PR_BODY:-Automated changes from factory agent.}"
        } > /data/pr_body.md

        set +e
        # A spec opens the PR as a draft; it's marked ready once the spec is built.
        DRAFT_FLAG=()
        if [ "${SPEC_MODE:-}" = "draft" ]; then DRAFT_FLAG=(--draft); fi
        PR_OUTPUT=$(gh pr create --repo "$REPO" --head "$BRANCH_NAME" "${DRAFT_FLAG[@]}" \
            --title "${PR_TITLE:-Agent: $BRANCH_NAME}" \
            --body-file /data/pr_body.md 2>&1)
        PR_STATUS=$?
        set -e
        echo "$PR_OUTPUT"
        # When the agent already opened the PR with its tools, gh fails with
        # "already exists" and that PR's URL, which is just as good.
        PR_NUM=$(echo "$PR_OUTPUT" | grep -oE '/pull/[0-9]+' | grep -oE '[0-9]+' | tail -1 || true)
        if [ -n "$PR_NUM" ]; then
            echo "FACTORY_PR_NUMBER=${PR_NUM}"
        else
            # Not fatal: the branch is pushed and the next turn retries the PR.
            echo "FACTORY_PR_CREATE_FAILED=1"
        fi
    fi
fi

if [ "$AGENT_EXIT" -ne 0 ]; then
    echo "FACTORY_AGENT_FAILED=1"
fi

echo "FACTORY_DONE=1"
