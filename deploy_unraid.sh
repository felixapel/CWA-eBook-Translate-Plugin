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

echo "Ensuring environment variables for book-translator-api..."
ssh $UNRAID_USER@$UNRAID_HOST "cat << 'EOF' > $API_DIR/.env
BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions
BT_BATCH_SIZE=5
BT_MAX_CONCURRENT=1
BT_TIMEOUT=120
BT_CONTEXT_WINDOW=1
LLM_PROVIDER=local
LLM_MODEL=gemma4-12b
EOF"

echo "Rebuilding and restarting backend container..."
ssh $UNRAID_USER@$UNRAID_HOST "cd $API_DIR && docker compose build && docker compose up -d"

# 2. Update frontend (CWA overlay)
echo "Backing up existing frontend scripts on Unraid..."
ssh $UNRAID_USER@$UNRAID_HOST "mkdir -p $CWA_OVERLAY_DIR/backups && cp $CWA_OVERLAY_DIR/translator.js $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.js || true"
ssh $UNRAID_USER@$UNRAID_HOST "cp $CWA_OVERLAY_DIR/translator.css $CWA_OVERLAY_DIR/backups/translator_$TIMESTAMP.css || true"

echo "Copying new frontend scripts to CWA overlay..."
scp static/translator.js static/translator.css $UNRAID_USER@$UNRAID_HOST:$CWA_OVERLAY_DIR/

echo "Deployment complete! Run verify_unraid.sh to check status."
