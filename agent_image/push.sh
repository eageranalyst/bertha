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
        git -c user.name="${BOT_NAME:-factory-agent}" -c user.email="${BOT_EMAIL:-factory-agent@users.noreply.github.com}" \
            commit -q -m "${COMMIT_MESSAGE:-Work in progress}"
    fi
    git bundle create -q '"$BUNDLE"' HEAD
' >/dev/null 2>/data/push_error.txt || {
    echo "PUSH_ERROR=couldn't commit the changes: $(tail -c 400 /data/push_error.txt | tr '\n' ' ')"
    exit 1
}

source /opt/factory/git_safe.sh
write_global_git_config
git init -q --bare /tmp/push
cd /tmp/push
git remote add origin "https://github.com/${REPO}.git"
git fetch -q "$BUNDLE" "HEAD:refs/heads/out"
rm -f "$BUNDLE"
if ! git push -q origin "refs/heads/out:refs/heads/$BRANCH_NAME" 2>/tmp/push_error.txt; then
    echo "PUSH_ERROR=$(tail -c 400 /tmp/push_error.txt | tr '\n' ' ')"
    exit 1
fi
echo "PUSHED_SHA=$(git rev-parse refs/heads/out)"
