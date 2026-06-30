#!/usr/bin/env bash
set -e

# Configuration
UNRAID_HOST="192.168.0.122"
UNRAID_USER="root"
CWA_OVERLAY_DIR="/mnt/user/appdata/calibre-web-automated/overlay"
API_DIR="/opt/book-translator"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

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
    -e BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions \
    -e BT_BATCH_SIZE=5 \
    -e BT_MAX_CONCURRENT=1 \
    -e BT_TIMEOUT=120 \
    -e BT_CONTEXT_WINDOW=1 \
    -e LLM_PROVIDER=local \
    -e LLM_MODEL=gemma4-12b \
    --restart=unless-stopped \
    local/book-translator-api:latest
"

# 2. Update frontend (CWA overlay)
echo "Backing up existing frontend scripts on Unraid..."
ssh $UNRAID_USER@$UNRAID_HOST "mkdir -p $CWA_OVERLAY_DIR/backups && cp $CWA_OVERLAY_DIR/translator.js $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.js || true"
ssh $UNRAID_USER@$UNRAID_HOST "cp $CWA_OVERLAY_DIR/translator.css $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.css || true"

echo "Copying new frontend scripts to CWA overlay..."
scp static/translator.js static/translator.css $UNRAID_USER@$UNRAID_HOST:$CWA_OVERLAY_DIR/

echo "Deployment complete! Run verify_unraid.sh to check status."
