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
