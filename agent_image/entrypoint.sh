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
# Permission posture: no --auto. The bash permission block below is a
# default-deny allowlist ("*": "ask", with specific safe patterns set
# to "allow") -- verified against this opencode build to auto-reject
# anything unmatched within a few seconds when run unattended, rather
# than hanging or silently allowing. Extend the allowlist if a repo
# needs another build tool; don't switch to --auto to work around it.
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

cat > "$HOME/.config/opencode/opencode.jsonc" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "${LOCAL_MODEL_PROVIDER}": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local model (factory)",
      "options": { "baseURL": "${LOCAL_MODEL_BASE_URL}" },
      "models": {
        "${LOCAL_MODEL_KEY}": { "id": "${LOCAL_MODEL_ID}", "name": "${LOCAL_MODEL_ID}" }
      }
    }
  },
  "permission": {
    "edit": "allow",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
    "bash": {
      "*": "ask",
      "git status*": "allow",
      "git diff*": "allow",
      "git log*": "allow",
      "git show*": "allow",
      "git add*": "allow",
      "git commit*": "allow",
      "git checkout*": "allow",
      "git branch*": "allow",
      "git fetch*": "allow",
      "git pull*": "allow",
      "git push*": "allow",
      "git merge*": "allow",
      "git rebase*": "allow",
      "git stash*": "allow",
      "git ls-remote*": "allow",
      "npm *": "allow",
      "npx *": "allow",
      "node *": "allow",
      "pip *": "allow",
      "pip3 *": "allow",
      "python*": "allow",
      "pytest*": "allow",
      "gh pr *": "allow",
      "gh issue *": "allow",
      "ls*": "allow",
      "cat *": "allow",
      "grep*": "allow",
      "find *": "allow",
      "mkdir *": "allow",
      "wc *": "allow",
      "head *": "allow",
      "tail *": "allow"
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

echo "=== Running agent (opencode session ${OPENCODE_SESSION_ID:-<new>}, model $OPENCODE_MODEL) ==="
set +e
if [ -n "${OPENCODE_SESSION_ID:-}" ]; then
    opencode run "$PROMPT" -s "$OPENCODE_SESSION_ID" -m "$OPENCODE_MODEL" \
        --format json > /data/last_result.ndjson
else
    opencode run "$PROMPT" -m "$OPENCODE_MODEL" \
        --format json > /data/last_result.ndjson
fi
AGENT_EXIT=$?
set -e
cat /data/last_result.ndjson
echo "=== Agent finished (exit $AGENT_EXIT) ==="

NEW_SESSION_ID=$(grep -m1 -o '"sessionID":"[^"]*"' /data/last_result.ndjson | head -1 | cut -d'"' -f4 || true)
if [ -n "$NEW_SESSION_ID" ]; then
    echo "FACTORY_OPENCODE_SESSION_ID=${NEW_SESSION_ID}"
fi

if [ -n "$REPO" ]; then
    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        git commit -m "${COMMIT_MESSAGE:-agent: apply changes for $BRANCH_NAME}" >/dev/null
        git push origin "$BRANCH_NAME"
        echo "FACTORY_PUSHED=1"
    else
        echo "FACTORY_PUSHED=0"
    fi

    if [ -z "${PR_NUMBER:-}" ] || [ "${PR_NUMBER}" = "0" ]; then
        set +e
        PR_OUTPUT=$(gh pr create --repo "$REPO" --head "$BRANCH_NAME" \
            --title "${PR_TITLE:-Agent: $BRANCH_NAME}" \
            --body "${PR_BODY:-Automated changes from factory agent.}" 2>&1)
        PR_STATUS=$?
        set -e
        echo "$PR_OUTPUT"
        if [ $PR_STATUS -eq 0 ]; then
            PR_NUM=$(echo "$PR_OUTPUT" | grep -oE '/pull/[0-9]+' | grep -oE '[0-9]+' | tail -1)
            echo "FACTORY_PR_NUMBER=${PR_NUM}"
        else
            echo "FACTORY_PR_CREATE_FAILED=1"
        fi
    fi
fi

if [ "$AGENT_EXIT" -ne 0 ]; then
    echo "FACTORY_AGENT_FAILED=1"
fi

echo "FACTORY_DONE=1"
