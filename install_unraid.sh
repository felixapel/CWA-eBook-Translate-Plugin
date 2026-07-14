#!/usr/bin/env bash
# Thin local wrapper for the authoritative btctl Unraid adapter.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -ne 1 ]; then
    echo "usage: $0 /absolute/private/path/install.env" >&2
    exit 64
fi
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "install_unraid.sh: run as root so appdata can be owned by uid 101 gid 102" >&2
    exit 77
fi

ENV_FILE="$1"
"$SCRIPT_DIR/btctl" --repository "$SCRIPT_DIR" plan --env "$ENV_FILE"
exec "$SCRIPT_DIR/btctl" --repository "$SCRIPT_DIR" \
    install --env "$ENV_FILE" --yes
