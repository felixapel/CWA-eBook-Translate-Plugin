#!/usr/bin/env bash
set -e

UNRAID_HOST="192.168.0.122"
UNRAID_USER="root"

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
