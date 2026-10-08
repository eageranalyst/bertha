#!/usr/bin/env bash
# Pushes a session's branch while its agent is mid-turn, for the push_branch
# tool (app/github_tools.py). Runs in its own short-lived container on the
# session's volume, so the agent's container never holds the token.
#
# The agent is still running and can change its checkout at any moment, so
# git never runs with the token there. Instead, as the agent's user and
# without the token, anything uncommitted is committed and the branch is
# written to a bundle; then, as root, the bundle is fetched into a fresh
# repository in this container and pushed from there: only to the session's
# branch, never forced.
set -euo pipefail
: "${GITHUB_TOKEN:?}" "${REPO:?}" "${BRANCH_NAME:?}"
BUNDLE=/data/push.bundle
AS_AGENT=(setpriv --reuid=node --regid=node --init-groups env -u GITHUB_TOKEN -u GH_TOKEN HOME=/data)

"${AS_AGENT[@]}" bash -c '
    set -e
    cd /data/repo
    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        git -c user.name="${BOT_NAME:?BOT_NAME is not set}" -c user.email="${BOT_EMAIL:?BOT_EMAIL is not set}" \
            commit -q -m "${COMMIT_MESSAGE:-Work in progress}"
    fi
    # Workflow files are never pushed (see entrypoint.sh): say so plainly.
    if git rev-parse -q --verify "refs/remotes/origin/$BRANCH_NAME" >/dev/null; then
        base="origin/$BRANCH_NAME"
    else
        base=$(git merge-base origin/HEAD HEAD 2>/dev/null || true)
    fi
    if [ -n "$base" ] && ! git diff --quiet "$base" HEAD -- .github/workflows; then
        echo "WORKFLOWS=$(git diff --name-only "$base" HEAD -- .github/workflows | tr "\n" " ")"
        exit 0
    fi
    git bundle create -q '"$BUNDLE"' HEAD
' >/data/push_check.txt 2>/data/push_error.txt || {
    echo "PUSH_ERROR=couldn't commit the changes: $(tail -c 400 /data/push_error.txt | tr '\n' ' ')"
    exit 1
}

if grep -q '^WORKFLOWS=' /data/push_check.txt; then
    echo "PUSH_ERROR=these commits change workflow files ($(sed -n 's/^WORKFLOWS=//p' /data/push_check.txt)), which can't be pushed mid-turn. Keep your workflow changes; when the turn ends, Bertha pushes everything else and posts the workflow change on the pull request for the user to apply. To push now, move the workflow edits out of your commits first."
    exit 1
fi

source /opt/factory/git_safe.sh
write_global_git_config
git init -q --bare /tmp/push
cd /tmp/push
git remote add origin "https://github.com/${REPO}.git"
git fetch -q "$BUNDLE" "HEAD:refs/heads/out"
rm -f "$BUNDLE"
if ! git push -q origin "refs/heads/out:refs/heads/$BRANCH_NAME" 2>/tmp/push_error.txt; then
    if grep -qE 'non-fast-forward|fetch first|remote contains work' /tmp/push_error.txt; then
        echo "PUSH_ERROR=the branch on GitHub has commits yours doesn't, usually because a commit that was already pushed was amended, reset or rebased. Don't rewrite pushed commits: add new commits on top instead. Bertha reconciles the branch with GitHub's copy when the turn ends."
    else
        echo "PUSH_ERROR=$(tail -c 400 /tmp/push_error.txt | tr '\n' ' ')"
    fi
    exit 1
fi
SHA=$(git rev-parse refs/heads/out)
# The agent's checkout now knows what GitHub has, so the end of the turn
# builds on it rather than on a stale copy.
"${AS_AGENT[@]}" git -C /data/repo update-ref "refs/remotes/origin/$BRANCH_NAME" "$SHA" 2>/dev/null || true
echo "PUSHED_SHA=$SHA"
