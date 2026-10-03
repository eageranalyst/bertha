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
#
# Two things worth knowing about how this matches:
# - Rules match the WHOLE command string, not each piped/chained part.
#   "pwd && ls -la" only matches if some rule matches that literal
#   string from the start -- "ls*" alone won't catch it. Rules are
#   evaluated in order with the LAST match winning, so the "deny"
#   patterns near the end are a backstop against an allowed prefix
#   (e.g. "cd *") hiding something dangerous after a "&&".
# - "echo" and "env"/"printenv" are deliberately NOT allowlisted:
#   GITHUB_TOKEN is a real env var in this container (needed for git
#   push / gh pr create), and both can read env vars straight into
#   the transcript ("echo $GITHUB_TOKEN"). They fall through to the
#   "ask" catch-all and auto-reject instead.
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
      "git ls-files*": "allow",
      "git ls-tree*": "allow",
      "git rev-parse*": "allow",
      "git rev-list*": "allow",
      "git grep*": "allow",
      "git blame*": "allow",
      "git shortlog*": "allow",
      "git describe*": "allow",
      "git cat-file*": "allow",
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
      "tail *": "allow",
      "pwd*": "allow",
      "which *": "allow",
      "date*": "allow",
      "true*": "allow",
      "type *": "allow",
      "file *": "allow",
      "du *": "allow",
      "df*": "allow",
      "diff *": "allow",
      "cd *": "allow",
      "*rm -rf*": "deny",
      "*rm -fr*": "deny",
      "*sudo *": "deny",
      "* dd *": "deny",
      "*mkfs*": "deny",
      "*chmod -R 777*": "deny",
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
    MESSAGE="You are working in the git checkout at /data/repo, on branch $BRANCH_NAME. Carry the task through to finished work in this one run:
- Make the change the task needs, and keep it focused on what was asked.
- Run the project's own tests or checks when you can, and fix anything you broke.
- Nobody will answer questions while you work. Where the task is ambiguous, pick the most reasonable reading and say what you chose.
- To search and read files, use the glob, grep and read tools. Use bash only for things they can't do, and keep each command simple: no pipelines built with commas, and no long find-and-sort chains.
- If a command is rejected, don't stop. Retry with a simpler command, or use another tool, and carry on with the task.
- Always finish with a final message, even if part of the task couldn't be done. It must answer the task with what you found, and say what you could not do. It becomes the pull request description.
- If the task needs no code change, say so in your final message and stop.

Task:
$PROMPT"
fi

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
    set +e
    if [ -n "$SESSION" ]; then
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" opencode run "$MESSAGE_THIS_RUN" \
            -s "$SESSION" -m "$OPENCODE_MODEL" --format json > "$RUN_FILE"
    else
        timeout "${AGENT_RUN_TIMEOUT_SECONDS:-900}" opencode run "$MESSAGE_THIS_RUN" \
            -m "$OPENCODE_MODEL" --format json > "$RUN_FILE"
    fi
    AGENT_EXIT=$?
    set -e
    cat "$RUN_FILE"
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
    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        git commit -m "${COMMIT_MESSAGE:-agent: apply changes for $BRANCH_NAME}" >/dev/null
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
        # The agent's final messages, which describe what it did.
        python3 - > /data/pr_summary.md <<'PY' || true
import json
texts = []
try:
    with open("/data/last_result.ndjson") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            part = event.get("part") or {}
            if event.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
except OSError:
    pass
print("\n".join(texts).strip()[-4000:])
PY
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
