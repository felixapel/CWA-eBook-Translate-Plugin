#!/usr/bin/env bash
# Personal deploy-verification helper for a specific Unraid box, kept here as a
# worked example. Adapt the paths/host for your own environment, or override
# via env vars: UNRAID_HOST=10.0.0.5 UNRAID_USER=myuser ./verify_unraid.sh
# (10.0.0.10 below is an example IP — substitute your Unraid host's LAN
# address; 192.168.x.x or 10.x.x.x both work as long as the translator
# container can reach it.)
set -e

UNRAID_HOST="${UNRAID_HOST:-10.0.0.10}"
UNRAID_USER="${UNRAID_USER:-root}"

echo "Verifying book-translator-api container health..."
ssh $UNRAID_USER@$UNRAID_HOST "docker ps | grep book-translator-api"

echo "Fetching translation backend status..."
ssh $UNRAID_USER@$UNRAID_HOST "curl -s http://localhost:8390/health | grep -i ok || echo 'Backend not responding correctly!'"

echo "Checking if CWA overlay has latest translator.js hash..."
SSH_HASH=$(ssh $UNRAID_USER@$UNRAID_HOST "sha256sum /mnt/user/appdata/calibre-web-automated/overlay/translator.js" | awk '{print $1}')
LOCAL_HASH=$(sha256sum static/translator.js | awk '{print $1}')

if [ "$SSH_HASH" == "$LOCAL_HASH" ]; then
    echo "Frontend hash matches ($SSH_HASH) - overlay is up to date."
else
    echo "ERROR: Frontend hash mismatch!"
    echo "  Local:  $LOCAL_HASH"
    echo "  Unraid: $SSH_HASH"
fi

echo "All checks completed."
