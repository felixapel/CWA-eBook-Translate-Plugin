#!/usr/bin/env bash
# Exercise the exact published image as independent, sandboxed API/proxy roles.
set -euo pipefail

SMOKE_IMAGE="${1:?usage: container-smoke.sh IMAGE PREFIX}"
SMOKE_PREFIX="${2:?usage: container-smoke.sh IMAGE PREFIX}"
if [[ ! "$SMOKE_PREFIX" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,48}$ ]]; then
    echo "invalid smoke prefix: $SMOKE_PREFIX" >&2
    exit 64
fi

API_CONTAINER="${SMOKE_PREFIX}-api"
PROXY_CONTAINER="${SMOKE_PREFIX}-proxy"
SMOKE_NETWORK="${SMOKE_PREFIX}-net"
SMOKE_VOLUME="${SMOKE_PREFIX}-data"
SMOKE_TOKEN="container-smoke-only-secret"

cleanup() {
    docker rm -f -v "$PROXY_CONTAINER" "$API_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm -f "$SMOKE_VOLUME" >/dev/null 2>&1 || true
    docker network rm "$SMOKE_NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

test "$(docker image inspect "$SMOKE_IMAGE" --format '{{.Config.User}}')" = "appuser"
docker network create "$SMOKE_NETWORK" >/dev/null
docker volume create "$SMOKE_VOLUME" >/dev/null

sandbox=(
    --read-only
    --tmpfs "/tmp:rw,noexec,nosuid,size=64m,uid=101,gid=102,mode=700"
    --cap-drop ALL
    --security-opt no-new-privileges:true
)

if invalid_output="$(docker run --rm --network "$SMOKE_NETWORK" \
    "${sandbox[@]}" \
    -e BT_ROLE=proxy \
    -e "CWA_UPSTREAM=http://${API_CONTAINER}:8390" \
    "$SMOKE_IMAGE" 2>&1)"; then
    echo "proxy unexpectedly started without BT_PUBLIC_ORIGIN" >&2
    exit 1
fi
grep -q 'BT_PUBLIC_ORIGIN' <<<"$invalid_output"
if grep -q 'Traceback' <<<"$invalid_output"; then
    echo "invalid proxy configuration exposed a traceback" >&2
    exit 1
fi

docker run -d --name "$API_CONTAINER" --network "$SMOKE_NETWORK" \
    "${sandbox[@]}" \
    --mount "type=volume,source=${SMOKE_VOLUME},target=/app/data" \
    -e BT_ROLE=api \
    -e BT_AUTH_MODE=token \
    -e "BT_API_TOKEN=${SMOKE_TOKEN}" \
    -p 127.0.0.1::8390 \
    "$SMOKE_IMAGE" >/dev/null

docker run -d --name "$PROXY_CONTAINER" --network "$SMOKE_NETWORK" \
    "${sandbox[@]}" \
    -e BT_ROLE=proxy \
    -e "CWA_UPSTREAM=http://${API_CONTAINER}:8390" \
    -e "BT_API_UPSTREAM=http://${API_CONTAINER}:8390" \
    -e BT_PUBLIC_ORIGIN=https://books.example.test:8443 \
    -p 127.0.0.1::8080 \
    "$SMOKE_IMAGE" >/dev/null

API_PORT="$(docker port "$API_CONTAINER" 8390/tcp | sed 's/.*://')"
PROXY_PORT="$(docker port "$PROXY_CONTAINER" 8080/tcp | sed 's/.*://')"
test -n "$API_PORT" && test -n "$PROXY_PORT"

for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${PROXY_PORT}/bt-api/ping" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -sf "http://127.0.0.1:${API_PORT}/ping" | grep -q '"status":"ok"'
curl -sf "http://127.0.0.1:${PROXY_PORT}/bt-api/ping" | grep -q '"status":"ok"'
curl -sf -H 'Host: attacker.example' -H 'X-Forwarded-Proto: javascript' \
    -H 'X-Forwarded-For: 203.0.113.99' \
    "http://127.0.0.1:${PROXY_PORT}/bt-api/ping" | grep -q '"status":"ok"'

# The generated configuration, not client-controlled forwarding headers, owns
# the public authority and the immediate client hop.
test "$(docker exec "$PROXY_CONTAINER" grep -Fc \
    'proxy_set_header Host books.example.test:8443;' /tmp/nginx/proxy.conf)" = "2"
test "$(docker exec "$PROXY_CONTAINER" grep -Fc \
    'proxy_set_header X-Forwarded-Proto https;' /tmp/nginx/proxy.conf)" = "2"
test "$(docker exec "$PROXY_CONTAINER" grep -Fc \
    'proxy_set_header X-Forwarded-For $remote_addr;' /tmp/nginx/proxy.conf)" = "2"
test "$(docker exec "$PROXY_CONTAINER" grep -Fc \
    'proxy_set_header Remote-User "";' /tmp/nginx/proxy.conf)" = "1"
docker exec "$PROXY_CONTAINER" grep -Fq \
    'client_max_body_size 2g;' /tmp/nginx/proxy.conf

# Protected surfaces reject anonymous callers through both published paths,
# while the configured compatibility token succeeds.
test "$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${API_PORT}/metrics")" = "401"
test "$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${PROXY_PORT}/bt-api/metrics")" = "401"
curl -sf -H "X-BT-Token: ${SMOKE_TOKEN}" \
    "http://127.0.0.1:${API_PORT}/metrics" | grep -q '"total_requests"'
curl -sf -H "X-BT-Token: ${SMOKE_TOKEN}" \
    "http://127.0.0.1:${PROXY_PORT}/bt-api/metrics" | grep -q '"total_requests"'

for container in "$API_CONTAINER" "$PROXY_CONTAINER"; do
    test "$(docker exec "$container" id -u)" = "101"
    test "$(docker exec "$container" id -g)" = "102"
    test "$(docker inspect "$container" --format '{{.HostConfig.ReadonlyRootfs}}')" = "true"
    docker inspect "$container" --format '{{json .HostConfig.CapDrop}}' | grep -q 'ALL'
    docker inspect "$container" --format '{{json .HostConfig.SecurityOpt}}' | grep -q 'no-new-privileges:true'
    if docker exec "$container" sh -c ': > /app/rootfs-write-probe' 2>/dev/null; then
        echo "$container unexpectedly wrote to the image rootfs" >&2
        exit 1
    fi
    docker exec "$container" sh -c ': > /tmp/write-probe && rm /tmp/write-probe'
done
if docker exec "$API_CONTAINER" sh -c 'test -e /app/test_auth.py -o -e /app/benchmark.py'; then
    echo "published runtime unexpectedly contains tests or benchmarks" >&2
    exit 1
fi
docker exec "$API_CONTAINER" sh -c \
    ': > /app/data/write-probe && rm /app/data/write-probe'
if docker inspect "$PROXY_CONTAINER" \
    --format '{{range .Mounts}}{{println .Destination}}{{end}}' \
    | grep -Fxq /app/data; then
    echo "proxy role unexpectedly mounts API state" >&2
    exit 1
fi

# Independent lifecycle: stopping the API must not stop nginx. The proxy then
# fails closed with 502 (immediate refusal) or 504 (bounded connect timeout).
docker stop --time 5 "$API_CONTAINER" >/dev/null
test "$(docker inspect "$PROXY_CONTAINER" --format '{{.State.Running}}')" = "true"
failure_status="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${PROXY_PORT}/bt-api/ping")"
case "$failure_status" in
    502|504) ;;
    *)
        echo "proxy returned $failure_status after API shutdown, expected 502/504" >&2
        exit 1
        ;;
esac
docker stop --time 5 "$PROXY_CONTAINER" >/dev/null

echo "container roles, sandbox, routing, and independent shutdown: OK"
