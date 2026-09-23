#!/bin/sh
# Fails when release-relevant files changed since the last tag (or the root
# commit, if no tag exists) but the plugin version was never bumped.
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

LAST_TAG="$(git tag -l 'v*' --sort=-creatordate | head -n 1)"
if [ -n "$LAST_TAG" ]; then
    REF="$LAST_TAG"
else
    REF="$(git rev-list --max-parents=0 HEAD | tail -n 1)"
fi

get_version() {
    git show "$1:.claude-plugin/plugin.json" 2>/dev/null \
        | grep '"version"' \
        | head -n 1 \
        | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/'
}

REF_VERSION="$(get_version "$REF")"
CUR_VERSION="$(get_version HEAD)"

CHANGED_FILES="$(git diff --name-only "$REF" HEAD -- mod skills hooks agents bin catalog .claude-plugin EXECUTOR.md)"

if [ -n "$CHANGED_FILES" ] && [ "$REF_VERSION" = "$CUR_VERSION" ]; then
    echo "release-check: release-relevant files changed since $REF but the plugin version ($CUR_VERSION) was not bumped:" >&2
    echo "$CHANGED_FILES" >&2
    exit 1
fi

exit 0
