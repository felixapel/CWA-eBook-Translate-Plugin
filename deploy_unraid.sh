#!/usr/bin/env bash
# Personal deploy helper for a specific Unraid box, kept here as a worked
# example of the rebuild-and-restart sequence. Adapt the paths/host/env for
# your own environment, or override the host via:
#   UNRAID_HOST=10.0.0.5 UNRAID_USER=myuser ./deploy_unraid.sh
# (10.0.0.10 below is just an example IP — substitute your Unraid host's LAN
# address; 192.168.x.x or 10.x.x.x both work as long as the translator
# container can reach it.)
set -e

# Configuration
UNRAID_HOST="${UNRAID_HOST:-10.0.0.10}"
UNRAID_USER="${UNRAID_USER:-root}"
CWA_OVERLAY_DIR="/mnt/user/appdata/calibre-web-automated/overlay"
API_DIR="/mnt/user/appdata/book-translator-api"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
# Host where your local LLM listens. INSIDE Docker, "localhost" is the
# container itself, not the Unraid host — point at the host's LAN IP or use
# host.docker.internal. The IP below is a placeholder; substitute your own.
LLM_HOST="${LLM_HOST:-10.0.0.10}"

echo "Starting deployment to $UNRAID_HOST..."

# 1. Update backend (book-translator-api)
echo "Updating backend code in $API_DIR..."
ssh $UNRAID_USER@$UNRAID_HOST "cd $API_DIR && git checkout main && git pull origin main"

echo "Rebuilding Docker image on Unraid..."
ssh $UNRAID_USER@$UNRAID_HOST "cd $API_DIR && docker build -t local/book-translator-api:latest ."

echo "Recreating container with correct environment variables..."
ssh $UNRAID_USER@$UNRAID_HOST "
  docker rm -f book-translator-api || true
  docker run -d \
    --name=book-translator-api \
    --net=bridge \
    -p 8390:8390 \
    -v /mnt/user/appdata/book-translator-api/data:/app/data \
    -e BT_LOCAL_URL=http://${LLM_HOST}:2819/v1/chat/completions \
    -e BT_BATCH_SIZE=3 \
    -e BT_MAX_CONCURRENT=1 \
    -e BT_TIMEOUT=60 \
    -e BT_CONTEXT_WINDOW=1 \
    -e BT_MAX_TOKENS=640 \
    -e BT_BATCH_MAX_TOKENS=1200 \
    -e LLM_PROVIDER=local \
    -e LLM_MODEL=gemma4-12b \
    -l net.unraid.docker.managed=dockerman \
    --restart=unless-stopped \
    local/book-translator-api:latest
  # Unraid-managed + autostart: without the dockerman label and the autostart
  # entry Unraid treats a hand-run container as an orphan and may remove it
  # (see docs/DEPLOY_UNRAID.md — this exact omission caused a vanished
  # container once).
  grep -qxF book-translator-api /var/lib/docker/unraid-autostart 2>/dev/null \
    || echo book-translator-api >> /var/lib/docker/unraid-autostart
"
# NOTE: BT_BATCH_SIZE=3 + BT_BATCH_MAX_TOKENS=1200 + BT_TIMEOUT=60 keep each vLLM
# call short enough to finish within the timeout even under ~8-way contention,
# which prevents the runaway "all slots stuck generating to max_tokens" spiral.
# These are the verified-stable values deployed 2026-06-30. Raise BT_BATCH_SIZE
# only if vLLM has spare capacity.

# 2. Update frontend (CWA overlay)
echo "Backing up existing frontend scripts on Unraid..."
ssh $UNRAID_USER@$UNRAID_HOST "mkdir -p $CWA_OVERLAY_DIR/backups && cp $CWA_OVERLAY_DIR/translator.js $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.js || true"
ssh $UNRAID_USER@$UNRAID_HOST "cp $CWA_OVERLAY_DIR/translator.css $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.css || true"
ssh $UNRAID_USER@$UNRAID_HOST "cp $CWA_OVERLAY_DIR/read.html $CWA_OVERLAY_DIR/backups/read_$TIMESTAMP.html || true"

echo "Copying new frontend scripts to CWA overlay..."
scp static/translator.js static/translator.css overlay/read.html $UNRAID_USER@$UNRAID_HOST:$CWA_OVERLAY_DIR/

echo "Restarting calibre-web-automated to inject overlay..."
ssh $UNRAID_USER@$UNRAID_HOST "docker restart calibre-web-automated"

echo "Deployment complete! Run verify_unraid.sh to check status."
