# Git as Bertha, holding the token, never under anything the agent wrote.
#
# The agent can write /data (it's $HOME and the checkout), so it could plant
# git config that runs commands (core.fsmonitor, filter drivers, credential
# helpers, url rewrites) or hooks that run on commit. Any of those would run
# with the token in the environment. So, before git runs with the token:
# - the global config lives outside /data, where the agent can't write;
# - the repo's own config is rewritten from scratch, with hooks off.
# Sourced by entrypoint.sh and push.sh.

export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/etc/factory/gitconfig
# gh reads its config (hosts, proxies, sockets) from here, not $HOME.
export GH_CONFIG_DIR=/etc/factory/gh
mkdir -p /etc/factory/gh

write_global_git_config() {
    : > "$GIT_CONFIG_GLOBAL"
    git config --global user.name "${BOT_NAME:-factory-agent}"
    git config --global user.email "${BOT_EMAIL:-factory-agent@users.noreply.github.com}"
    git config --global credential.helper \
        '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
    git config --global --add safe.directory /data/repo
    git config --global core.hooksPath /dev/null
}

# Brings the checked-out branch in line with GitHub's copy (origin/<branch>,
# fetched already) without losing work on either side:
# - GitHub's copy already in the branch (equal, or the branch is ahead): nothing;
# - the branch behind: fast-forward;
# - diverged (someone pushed to the branch, or the agent rewrote commits it had
#   pushed): merge GitHub's copy in, taking this side where they conflict.
# Returns 1, with the merge undone, when even that fails.
sync_with_remote() {
    local remote="origin/$1"
    git rev-parse -q --verify "refs/remotes/$remote" >/dev/null || return 0
    if git merge-base --is-ancestor "$remote" HEAD; then
        return 0
    fi
    if git merge-base --is-ancestor HEAD "$remote"; then
        git merge -q --ff-only "$remote"
        return 0
    fi
    if git merge -q --no-edit -X ours -m "Merge GitHub's $1 into this turn's work" "$remote" >/dev/null 2>&1; then
        echo "FACTORY_BRANCH_MERGED=1"
        return 0
    fi
    git merge --abort 2>/dev/null || true
    echo "FACTORY_BRANCH_DIVERGED=1"
    return 1
}

# Takes CI workflow changes out of the commits not yet on GitHub, in the
# current directory's repo: those commits are squashed into one without them,
# the files go back to how GitHub has them, and the change is printed as
# FACTORY_WORKFLOW_HELD (the files) and FACTORY_WORKFLOW_PATCH (the diff, in
# base64) for Bertha to post. Does nothing when no workflow changed.
hold_back_workflows() {
    local branch="$1" base held
    if git rev-parse -q --verify "refs/remotes/origin/$branch" >/dev/null; then
        base="origin/$branch"
    else
        base=$(git merge-base origin/HEAD HEAD 2>/dev/null || true)
    fi
    if [ -z "$base" ] || git diff --quiet "$base" HEAD -- .github/workflows; then
        return 0
    fi
    held=$(git diff --name-only "$base" HEAD -- .github/workflows | tr '\n' ' ')
    git diff "$base" HEAD -- .github/workflows > /data/workflow.patch
    git log --reverse --format=%B "$base..HEAD" > /data/held_messages.txt
    git reset -q --soft "$base"
    git rm -r -q --cached .github/workflows 2>/dev/null || true
    git checkout "$base" -- .github/workflows 2>/dev/null || true
    git ls-files --others --exclude-standard -- .github/workflows | xargs -r rm -f
    if ! git diff --cached --quiet; then
        git commit -q -F /data/held_messages.txt
    fi
    echo "FACTORY_WORKFLOW_HELD=$held"
    echo "FACTORY_WORKFLOW_PATCH=$(base64 -w0 /data/workflow.patch)"
}

# Replaces the checkout's .git/config with a minimal one for $REPO.
reset_repo_git_config() {
    local dir="${1:-/data/repo}"
    [ -d "$dir/.git" ] || return 0
    cat > "$dir/.git/config" <<GITCONFIG
[core]
	repositoryformatversion = 0
	filemode = true
	bare = false
	logallrefupdates = true
	hooksPath = /dev/null
[remote "origin"]
	url = https://github.com/${REPO}.git
	fetch = +refs/heads/*:refs/remotes/origin/*
GITCONFIG
}
