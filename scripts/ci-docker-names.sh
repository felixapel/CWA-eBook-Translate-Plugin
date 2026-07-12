#!/bin/sh
set -eu

# GitHub/Gitea run IDs are scoped to one repository. Hash the complete run
# identity so jobs sharing one Docker daemon cannot reuse or remove another
# repo's resources, while keeping Docker names short and restricted to safe
# bytes even if a provider exposes unusually long numeric identifiers.
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

run_scope=$(
    printf '%s\n%s\n%s\n' \
        "$GITHUB_REPOSITORY" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" \
        | sha256sum | cut -c1-20
)
case "$run_scope" in
    ""|*[!0-9a-f]*)
        echo "failed to derive a safe run scope" >&2
        exit 1
        ;;
esac

{
    printf 'SMOKE_PREFIX=bt-ci-%s\n' "$run_scope"
    printf 'SMOKE_IMAGE=bt-audit:%s\n' "$run_scope"
} >> "$GITHUB_ENV"
