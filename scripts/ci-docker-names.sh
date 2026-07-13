#!/bin/sh
set -eu

# GitHub/Gitea run IDs are scoped to one repository. Hash the repository slug
# so jobs sharing one Docker daemon cannot reuse or remove another repo's
# container, while keeping Docker names short and restricted to safe bytes.
if [ -z "${GITHUB_REPOSITORY:-}" ]; then
    echo "GITHUB_REPOSITORY is required" >&2
    exit 1
fi
if [ -z "${GITHUB_ENV:-}" ]; then
    echo "GITHUB_ENV is required" >&2
    exit 1
fi
case "${GITHUB_RUN_ID:-}" in
    ""|*[!0-9]*)
        echo "GITHUB_RUN_ID must be numeric" >&2
        exit 1
        ;;
esac
case "${GITHUB_RUN_ATTEMPT:-}" in
    ""|*[!0-9]*)
        echo "GITHUB_RUN_ATTEMPT must be numeric" >&2
        exit 1
        ;;
esac
if ! command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum is required" >&2
    exit 1
fi

repository_scope=$(
    printf '%s' "$GITHUB_REPOSITORY" | sha256sum | cut -c1-16
)
case "$repository_scope" in
    ""|*[!0-9a-f]*)
        echo "failed to derive a safe repository scope" >&2
        exit 1
        ;;
esac

{
    printf 'SMOKE_CONTAINER=bt-smoke-%s-%s-%s\n' \
        "$repository_scope" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"
    printf 'SMOKE_IMAGE=bt-audit:%s-%s-%s\n' \
        "$repository_scope" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"
} >> "$GITHUB_ENV"
