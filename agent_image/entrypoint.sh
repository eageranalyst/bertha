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
print(json.dumps({m["key"]: {"id": m["model_id"], "name": m["model_id"]} for m in models}))
')

# What Bertha knows about this repo (its wiki), loaded with the working
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
  "skills": { "paths": ["${HOME}/skills"] },
  "agent": { "build": { "steps": ${AGENT_STEPS:-25} } },
  "tool_output": { "max_lines": 200, "max_bytes": 8192 },
  "instructions": ["/opt/factory/WORKING.md", "${HOME}/factory/repo-context.md"],
  "compaction": { "auto": true },
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

git config --global user.name "${BOT_NAME:-factory-agent}"
git config --global user.email "${BOT_EMAIL:-factory-agent@users.noreply.github.com}"
git config --global credential.helper \
    '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
git config --global --add safe.directory /data/repo

cd /data
if [ -n "$REPO" ]; then
    if [ ! -d repo/.git ]; then
        echo "=== Cloning $REPO ==="
        git clone "https://github.com/${REPO}.git" repo
    fi
    cd repo
    if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
        git fetch origin "$BRANCH_NAME"
        git checkout -B "$BRANCH_NAME" "origin/$BRANCH_NAME"
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

# A repo turn is one shot: nobody answers questions while the agent works,
# so the instructions say to finish the task in this run and state what was
# done. The final message is reused as the pull request description.
MESSAGE="$PROMPT"
if [ -n "$REPO" ]; then
    # The general working practices come from WORKING.md (see "instructions"
    # in the config); this only adds what's specific to this turn.
    MESSAGE="You are working in the git checkout at /data/repo, on branch $BRANCH_NAME. Carry the task through to finished, verified work in this one run, following your working practices. Your changes are committed and proposed as a pull request when you finish. If the task needs no code change, answer it and say so.

Task:
$PROMPT"
fi

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
STATE="unfinished"
AGENT_EXIT=0
while true; do
    echo "=== Running agent (session ${SESSION:-<new>}, model $OPENCODE_MODEL, run $((CONTINUATIONS_USED + 1))) ==="
    : > "$RUN_FILE"
    # The events go to stdout as they happen (Bertha streams the container's
    # output into the transcript) and to the run file for the summary below.
    set +e
    if [ -n "$SESSION" ]; then
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" "${AS_AGENT[@]}" opencode run "$MESSAGE_THIS_RUN" \
            -s "$SESSION" -m "$OPENCODE_MODEL" --format json | tee "$RUN_FILE"
    else
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" "${AS_AGENT[@]}" opencode run "$MESSAGE_THIS_RUN" \
            -m "$OPENCODE_MODEL" --format json | tee "$RUN_FILE"
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
    TOKENS_USED=$((TOKENS_USED + ${TOKENS:-0}))
    echo "=== Run finished (exit $AGENT_EXIT, $STATE, ${TOKENS:-0} tokens; turn total $TOKENS_USED) ==="

    if [ "$AGENT_EXIT" -eq 124 ]; then
        echo "FACTORY_RUN_TIMED_OUT=1"
        break
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
    if [ -n "$REJECTED" ] && [ "$REJECTED" = "$PREVIOUS_REJECTED" ]; then
        echo "FACTORY_CONTINUE_STOPPED=the same command was rejected again"
        break
    fi
    PREVIOUS_REJECTED="$REJECTED"
    CONTINUATIONS_USED=$((CONTINUATIONS_USED + 1))
    MESSAGE_THIS_RUN="The previous run stopped before you answered, usually because a command was rejected. Carry on with the task: use the glob, grep and read tools, or a simpler command, and finish with a final answer."
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

    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        {
            echo "${PR_TITLE:-agent: changes for $BRANCH_NAME}"
            if [ -s /data/pr_summary.md ]; then echo; cat /data/pr_summary.md; fi
        } > /data/commit_message.txt
        git commit -F /data/commit_message.txt >/dev/null
        echo "FACTORY_PUSHED=1"
    else
        echo "FACTORY_PUSHED=0"
    fi

    # Commits this branch has beyond the default branch. Zero means the
    # agent changed nothing, which is a normal outcome, not a failure.
    AHEAD=$(git rev-list --count "origin/HEAD..HEAD" 2>/dev/null || echo 0)
    if [ "$AHEAD" -eq 0 ]; then
        echo "FACTORY_NO_CHANGES=1"
    else
        git push origin "$BRANCH_NAME"
    fi

    if [ "$AHEAD" -gt 0 ] && { [ -z "${PR_NUMBER:-}" ] || [ "${PR_NUMBER}" = "0" ]; }; then
        {
            if [ -s /data/pr_summary.md ]; then cat /data/pr_summary.md; echo; fi
            echo "${PR_BODY:-Automated changes from factory agent.}"
        } > /data/pr_body.md

        set +e
        PR_OUTPUT=$(gh pr create --repo "$REPO" --head "$BRANCH_NAME" \
            --title "${PR_TITLE:-Agent: $BRANCH_NAME}" \
            --body-file /data/pr_body.md 2>&1)
        PR_STATUS=$?
        set -e
        echo "$PR_OUTPUT"
        if [ $PR_STATUS -eq 0 ]; then
            PR_NUM=$(echo "$PR_OUTPUT" | grep -oE '/pull/[0-9]+' | grep -oE '[0-9]+' | tail -1)
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
