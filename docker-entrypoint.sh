#!/bin/sh
# book-translator entrypoint.
#
# Always runs the translation API (gunicorn). When CWA_UPSTREAM is set it also
# renders and starts the injection proxy (nginx) — see proxy/nginx.conf.template.
# A monitor loop exits the container if either process dies, so the
# orchestrator's restart policy can do its job instead of the container
# lingering half-alive.
#
# gunicorn runs as `appuser` (unprivileged) via gosu. nginx runs as root
# because it needs to bind the listen port and write to /run/nginx +
# /var/log/nginx. If you only need the API (no proxy), set CWA_UPSTREAM=""
# and nginx won't be started; the API still runs unprivileged.
set -eu

PORT="${PORT:-8390}"
BT_PROXY_PORT="${BT_PROXY_PORT:-8080}"
BT_UI_VERSION="$(cat /app/VERSION 2>/dev/null || echo dev)"
export PORT BT_PROXY_PORT BT_UI_VERSION

# server.py reads the trusted-proxy allowlist at import time, so proxy-mode
# defaults must exist before Gunicorn imports the application. Every API
# request in this mode arrives from the in-container nginx on loopback; trust
# only that peer and key rate limits on the last X-Forwarded-For hop nginx
# observed. An explicitly supplied allowlist still takes precedence.
if [ -n "${CWA_UPSTREAM:-}" ]; then
    export BT_TRUSTED_PROXIES="${BT_TRUSTED_PROXIES:-127.0.0.1/32}"
fi

# The data dir is almost always a BIND MOUNT owned by the host (root, or an
# appdata user) — the build-time chown only covers the image's own layer, so
# without this runtime chown gunicorn (running as appuser) cannot open the
# SQLite database and the worker dies at boot ("unable to open database
# file"). Reproduced with `-v /root-owned-dir:/app/data`; anonymous volumes
# masked it because they inherit the image's ownership.
mkdir -p /app/data
# Only repair ownership when appuser actually lacks write access, and only on
# the flat data dir + its immediate files (no -R: recursive chown on a big
# host appdata tree is slow and mutates host-side ownership more than needed).
if ! gosu appuser sh -c 'test -w /app/data && { test ! -e /app/data/translations.db || test -w /app/data/translations.db; }'; then
    chown appuser:appuser /app/data /app/data/* /app/data/.[!.]* 2>/dev/null || true
    gosu appuser sh -c 'test -w /app/data' || \
        echo "[entrypoint] WARNING: could not make /app/data writable (read-only mount?) — the API may fail to write its cache"
fi

# Drop privileges for gunicorn. `gosu` (vs su) does not leak env or fork a
# tty, and forwards signals (SIGTERM) cleanly to the child process so
# `docker stop` reaches gunicorn directly.
gosu appuser gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 --timeout 120 server:app &
API_PID=$!

NGINX_PID=""
if [ -n "${CWA_UPSTREAM:-}" ]; then
    echo "[entrypoint] proxy mode: :${BT_PROXY_PORT} -> ${CWA_UPSTREAM} (overlay injected)"
    mkdir -p /run/nginx
    # Substitute ONLY our variables; nginx's own $vars must survive verbatim.
    # Literal ${...} names below are envsubst's allowlist, not shell expansion.
    # shellcheck disable=SC2016
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
