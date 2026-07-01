#!/bin/sh
# book-translator entrypoint.
#
# Always runs the translation API (gunicorn). When CWA_UPSTREAM is set it also
# renders and starts the injection proxy (nginx) — see proxy/nginx.conf.template.
# A monitor loop exits the container if either process dies, so the
# orchestrator's restart policy can do its job instead of the container
# lingering half-alive.
set -eu

PORT="${PORT:-8390}"
BT_PROXY_PORT="${BT_PROXY_PORT:-8080}"
BT_UI_VERSION="$(cat /app/VERSION 2>/dev/null || echo dev)"
export PORT BT_PROXY_PORT BT_UI_VERSION

gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 --timeout 120 server:app &
API_PID=$!

NGINX_PID=""
if [ -n "${CWA_UPSTREAM:-}" ]; then
    echo "[entrypoint] proxy mode: :${BT_PROXY_PORT} -> ${CWA_UPSTREAM} (overlay injected)"
    mkdir -p /run/nginx
    # Substitute ONLY our variables; nginx's own $vars must survive verbatim.
    envsubst '${CWA_UPSTREAM} ${PORT} ${BT_PROXY_PORT} ${BT_UI_VERSION}' \
        < /app/proxy/nginx.conf.template > /etc/nginx/http.d/default.conf
    nginx -t
    nginx -g 'daemon off;' &
    NGINX_PID=$!
else
    echo "[entrypoint] API-only mode (set CWA_UPSTREAM to enable the injection proxy)"
fi

shutdown() {
    kill -TERM "$API_PID" ${NGINX_PID:+$NGINX_PID} 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    [ -z "$NGINX_PID" ] || wait "$NGINX_PID" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

# Monitor: if any child dies, exit so the container restarts as a whole.
while :; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "[entrypoint] gunicorn exited — stopping container"
        exit 1
    fi
    if [ -n "$NGINX_PID" ] && ! kill -0 "$NGINX_PID" 2>/dev/null; then
        echo "[entrypoint] nginx exited — stopping container"
        exit 1
    fi
    sleep 5 &
    wait $! || true
done
