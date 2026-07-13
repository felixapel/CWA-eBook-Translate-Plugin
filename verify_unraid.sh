#!/usr/bin/env bash
# Personal deploy-verification helper for a specific Unraid box, kept here as a
# worked example. Adapt the paths/host for your own environment, or override
# via env vars: UNRAID_HOST=10.0.0.5 UNRAID_USER=myuser ./verify_unraid.sh
# (10.0.0.10 below is an example IP — substitute your Unraid host's LAN
# address; 192.168.x.x or 10.x.x.x both work as long as the translator
# container can reach it.)
set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-10.0.0.10}"
UNRAID_USER="${UNRAID_USER:-root}"
REMOTE="${UNRAID_USER}@${UNRAID_HOST}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Verifying book-translator-api container health..."
ssh -- "$REMOTE" bash -s -- <<'REMOTE_SCRIPT'
set -euo pipefail

docker ps \
    --filter 'name=^/book-translator-api$' \
    --format '{{.Names}}' \
  | grep -Fxq book-translator-api

echo "Fetching translation backend status..."
curl --fail --silent --show-error --max-time 10 \
    http://127.0.0.1:8390/health \
  | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
REMOTE_SCRIPT

echo "Checking if CWA overlay has latest translator.js hash..."
SSH_HASH="$(
    ssh -- "$REMOTE" \
        sha256sum /mnt/user/appdata/calibre-web-automated/overlay/translator.js \
      | awk '{print $1}'
)"
LOCAL_HASH="$(sha256sum "$SCRIPT_DIR/static/translator.js" | awk '{print $1}')"

if [ "$SSH_HASH" = "$LOCAL_HASH" ]; then
    echo "Frontend hash matches ($SSH_HASH) - overlay is up to date."
else
    echo "ERROR: Frontend hash mismatch!"
    echo "  Local:  $LOCAL_HASH"
    echo "  Unraid: $SSH_HASH"
    exit 1
fi

echo "All checks completed."
