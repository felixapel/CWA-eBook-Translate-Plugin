#!/usr/bin/env bash
# Personal deploy helper for a specific Unraid box, kept here as a worked
# example of the rebuild-and-restart sequence. Adapt the paths/host/env for
# your own environment, or override the host via:
#   UNRAID_HOST=10.0.0.5 UNRAID_USER=myuser ./deploy_unraid.sh
# (10.0.0.10 below is just an example IP — substitute your Unraid host's LAN
# address; 192.168.x.x or 10.x.x.x both work as long as the translator
# container can reach it.)
set -euo pipefail

# Configuration
UNRAID_HOST="${UNRAID_HOST:-10.0.0.10}"
UNRAID_USER="${UNRAID_USER:-root}"
CWA_OVERLAY_DIR="/mnt/user/appdata/calibre-web-automated/overlay"
API_DIR="/mnt/user/appdata/book-translator-api"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="${UNRAID_USER}@${UNRAID_HOST}"
# Host where your local LLM listens. INSIDE Docker, "localhost" is the
# container itself, not the Unraid host — point at the host's LAN IP or use
# host.docker.internal. The IP below is a placeholder; substitute your own.
LLM_HOST="${LLM_HOST:-10.0.0.10}"

echo "Starting deployment to $UNRAID_HOST..."

# 1. Update backend (book-translator-api)
echo "Updating backend code in $API_DIR..."
{
    # Bash %q serializes values as one shell word. The remote script itself is
    # a quoted heredoc, so operator-controlled values are data, never code.
    printf 'api_dir=%q\n' "$API_DIR"
    printf 'llm_host=%q\n' "$LLM_HOST"
    cat <<'REMOTE_SCRIPT'
set -euo pipefail

cd "$api_dir"
git checkout main
git pull --ff-only origin main

echo "Rebuilding Docker image on Unraid..."
docker build -t local/book-translator-api:latest .

echo "Recreating container with correct environment variables..."
docker rm -f book-translator-api >/dev/null 2>&1 || true
docker run -d \
  --name=book-translator-api \
  --net=bridge \
  -p 8390:8390 \
  -v /mnt/user/appdata/book-translator-api/data:/app/data \
  -e "BT_LOCAL_URL=http://${llm_host}:2819/v1/chat/completions" \
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
# entry Unraid treats a hand-run container as an orphan and may remove it.
grep -qxF book-translator-api /var/lib/docker/unraid-autostart 2>/dev/null \
  || echo book-translator-api >> /var/lib/docker/unraid-autostart
REMOTE_SCRIPT
} | ssh -- "$REMOTE" bash -s --
# NOTE: BT_BATCH_SIZE=3 + BT_BATCH_MAX_TOKENS=1200 + BT_TIMEOUT=60 keep each vLLM
# call short enough to finish within the timeout even under ~8-way contention,
# which prevents the runaway "all slots stuck generating to max_tokens" spiral.
# These are the verified-stable values deployed 2026-06-30. Raise BT_BATCH_SIZE
# only if vLLM has spare capacity.

# 2. Update frontend (CWA overlay)
echo "Backing up existing frontend scripts on Unraid..."
{
    printf 'overlay_dir=%q\n' "$CWA_OVERLAY_DIR"
    printf 'timestamp=%q\n' "$TIMESTAMP"
    cat <<'REMOTE_SCRIPT'
set -euo pipefail

mkdir -p "$overlay_dir/backups"
for asset in translator.js translator.css read.html; do
    if [ -f "$overlay_dir/$asset" ]; then
        stem="${asset%.*}"
        extension="${asset##*.}"
        cp -- "$overlay_dir/$asset" \
            "$overlay_dir/backups/${stem}_${timestamp}.${extension}"
    fi
done
REMOTE_SCRIPT
} | ssh -- "$REMOTE" bash -s --

echo "Copying new frontend scripts to CWA overlay..."
scp -- \
    "$SCRIPT_DIR/static/translator.js" \
    "$SCRIPT_DIR/static/translator.css" \
    "$SCRIPT_DIR/overlay/read.html" \
    "${REMOTE}:${CWA_OVERLAY_DIR}/"

echo "Restarting calibre-web-automated to inject overlay..."
ssh -- "$REMOTE" docker restart calibre-web-automated

echo "Deployment complete! Run verify_unraid.sh to check status."
