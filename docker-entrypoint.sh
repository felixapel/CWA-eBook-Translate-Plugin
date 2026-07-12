#!/bin/sh
# Non-root runtime dispatcher for API, proxy, or legacy combined mode.
set -eu

PORT="${PORT:-8390}"
BT_PROXY_PORT="${BT_PROXY_PORT:-8080}"
BT_ROLE="${BT_ROLE:-auto}"
BT_UI_VERSION="$(cat /app/VERSION 2>/dev/null || echo dev)"
export PORT BT_PROXY_PORT BT_ROLE BT_UI_VERSION

if [ "$BT_ROLE" = "auto" ]; then
    if [ -n "${CWA_UPSTREAM:-}" ]; then
        BT_ROLE="all"
    else
        BT_ROLE="api"
    fi
    export BT_ROLE
fi

case "$BT_ROLE" in
    api|proxy|all) ;;
    *)
        echo "[entrypoint] ERROR: BT_ROLE must be api, proxy, all, or auto" >&2
        exit 64
        ;;
esac

if [ "$BT_ROLE" = "all" ]; then
    export BT_TRUSTED_PROXIES="${BT_TRUSTED_PROXIES:-127.0.0.1/32}"
fi

check_data_dir() {
    if [ ! -d /app/data ]; then
        echo "[entrypoint] ERROR: /app/data is missing" >&2
        exit 78
    fi
    probe="/app/data/.write-probe.$$"
    if ! (umask 077 && : > "$probe") 2>/dev/null; then
        echo "[entrypoint] ERROR: /app/data must be writable by uid 101 gid 102" >&2
        exit 78
    fi
    rm -f "$probe"
}

configure_proxy() {
    if [ -z "${CWA_UPSTREAM:-}" ]; then
        echo "[entrypoint] ERROR: CWA_UPSTREAM is required for the proxy role" >&2
        exit 64
    fi
    BT_API_UPSTREAM="${BT_API_UPSTREAM:-http://127.0.0.1:${PORT}}"
    export BT_API_UPSTREAM

    mkdir -p \
        /tmp/nginx/client_temp \
        /tmp/nginx/proxy_temp \
        /tmp/nginx/fastcgi_temp \
        /tmp/nginx/uwsgi_temp \
        /tmp/nginx/scgi_temp
    # Literal ${...} names below are envsubst's allowlist.
    # shellcheck disable=SC2016
    envsubst '${CWA_UPSTREAM} ${BT_API_UPSTREAM} ${BT_PROXY_PORT} ${BT_UI_VERSION}' \
        < /app/proxy/nginx.conf.template > /tmp/nginx/proxy.conf
    nginx -t -c /app/proxy/nginx-main.conf -e /dev/stderr
}

start_api() {
    gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 \
        --timeout 120 server:app
}

start_proxy() {
    nginx -c /app/proxy/nginx-main.conf -e /dev/stderr -g 'daemon off;'
}

case "$BT_ROLE" in
    api)
        check_data_dir
        echo "[entrypoint] API role on :${PORT}"
        exec gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 \
            --timeout 120 server:app
        ;;
    proxy)
        configure_proxy
        echo "[entrypoint] proxy role on :${BT_PROXY_PORT} -> ${CWA_UPSTREAM}"
        exec nginx -c /app/proxy/nginx-main.conf -e /dev/stderr -g 'daemon off;'
        ;;
esac

# Legacy one-container compatibility. It is non-root, but the recommended
# topology uses two role-specific containers so each has its own health and
# restart lifecycle.
check_data_dir
configure_proxy
echo "[entrypoint] combined role: API :${PORT}, proxy :${BT_PROXY_PORT}"
start_api &
API_PID=$!
start_proxy &
NGINX_PID=$!

stop_children() {
    kill -TERM "$API_PID" "$NGINX_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    wait "$NGINX_PID" 2>/dev/null || true
}

shutdown() {
    stop_children
    exit 0
}
trap shutdown TERM INT

while :; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "[entrypoint] gunicorn exited; stopping combined container" >&2
        stop_children
        exit 1
    fi
    if ! kill -0 "$NGINX_PID" 2>/dev/null; then
        echo "[entrypoint] nginx exited; stopping combined container" >&2
        stop_children
        exit 1
    fi
    sleep 5 &
    wait $! || true
done
