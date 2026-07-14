#!/usr/bin/env bash
# Prove the public dispatcher works on a root host with Docker and no Python.
set -euo pipefail

SMOKE_PREFIX="${1:?usage: btctl-bootstrap-smoke.sh PREFIX}"
if [[ ! "$SMOKE_PREFIX" =~ ^[a-z0-9][a-z0-9.-]{0,32}$ ]]; then
    printf 'invalid btctl bootstrap smoke prefix: %s\n' "$SMOKE_PREFIX" >&2
    exit 64
fi

ROOT_DIR="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REVISION="$(git -C "$ROOT_DIR" rev-parse HEAD)"
VERSION="$(tr -d '\r\n' <"$ROOT_DIR/VERSION")"
DISPATCHER_IMAGE="local/cwa-translate-btctl-smoke:${SMOKE_PREFIX}"
SOURCE_IMAGE="local/cwa-translate-btctl-source-smoke:${SMOKE_PREFIX}"
SHARE_ROOT="${SMOKE_PREFIX}-btctl"
CWA_NETWORK="${SMOKE_PREFIX}-cwa-net"
CWA_CONTAINER="${SMOKE_PREFIX}-cwa"
CWA_IMAGE="${SMOKE_PREFIX}-cwa-fixture:4.0.6"
INSTALL_NAME="${SMOKE_PREFIX}-managed"
PRODUCTION_IMAGE="local/cwa-translate:${VERSION}-${REVISION:0:12}"
TEMPLATE_ROOT=/boot/config/plugins/dockerMan/templates-user
HOST_LOCK_DIRECTORY=/run/cwa-translate-btctl-locks
TEMPORARY="$(mktemp -d "${TMPDIR:-/tmp}/btctl-bootstrap-smoke.XXXXXX")"
ENV_FILE="$TEMPORARY/install.env"
OUTPUT="$TEMPORARY/plan.json"
CWA_FIXTURE="$TEMPORARY/cwa-fixture.py"
share_created=0
image_created=0
source_image_created=0
cwa_image_created=0
template_root_created=0
template_dockerman_created=0
template_plugins_created=0
template_config_created=0
template_files_reserved=0
production_image_preexisting=0
lock_directory_created=0
PROXY_PORT="$(python3 - <<'PY'
import socket

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"

if docker image inspect "$PRODUCTION_IMAGE" >/dev/null 2>&1; then
    production_image_preexisting=1
fi

cleanup() {
    local status=$?
    local cleanup_failed=0
    trap - EXIT HUP INT TERM
    docker rm -f -v \
        "${INSTALL_NAME}-proxy" "${INSTALL_NAME}-api" "$CWA_CONTAINER" \
        >/dev/null 2>&1 || true
    docker network rm "${INSTALL_NAME}-private" "$CWA_NETWORK" \
        >/dev/null 2>&1 || true
    if [ "$template_files_reserved" -eq 1 ] && [ "$source_image_created" -eq 1 ]; then
        docker run --rm --user 0:0 --network none \
            --read-only --cap-drop ALL \
            --security-opt no-new-privileges:true \
            --mount type=bind,src=/boot,dst=/host-boot \
            --entrypoint /bin/sh "$SOURCE_IMAGE" \
            -ec 'rm -f -- \
                /host-boot/config/plugins/dockerMan/templates-user/my-cwa-translate-api.xml \
                /host-boot/config/plugins/dockerMan/templates-user/my-cwa-translate-proxy.xml; \
                if [ "$1" = 1 ]; then \
                    rmdir /host-boot/config/plugins/dockerMan/templates-user \
                        2>/dev/null || true; \
                fi; \
                if [ "$2" = 1 ]; then \
                    rmdir /host-boot/config/plugins/dockerMan 2>/dev/null || true; \
                fi; \
                if [ "$3" = 1 ]; then \
                    rmdir /host-boot/config/plugins 2>/dev/null || true; \
                fi; \
                if [ "$4" = 1 ]; then \
                    rmdir /host-boot/config 2>/dev/null || true; \
                fi' sh \
                "$template_root_created" \
                "$template_dockerman_created" \
                "$template_plugins_created" \
                "$template_config_created" \
            >/dev/null 2>&1 || true
    fi
    if [ "$share_created" -eq 1 ] && [ "$source_image_created" -eq 1 ]; then
        docker run --rm --user 0:0 --network none \
            --read-only --cap-drop ALL --cap-add DAC_OVERRIDE \
            --security-opt no-new-privileges:true \
            --mount type=bind,src=/mnt,dst=/host-mnt \
            --entrypoint /bin/sh "$SOURCE_IMAGE" \
            -ec 'rm -rf -- "/host-mnt/$1"' sh "$SHARE_ROOT" \
            >/dev/null 2>&1 || cleanup_failed=1
        if [ -e "/mnt/$SHARE_ROOT" ]; then
            cleanup_failed=1
        fi
    fi
    if [ "$production_image_preexisting" -eq 0 ]; then
        docker image rm "$PRODUCTION_IMAGE" >/dev/null 2>&1 || true
    fi
    if [ "$lock_directory_created" -eq 1 ] && [ "$source_image_created" -eq 1 ]; then
        docker run --rm --user 0:0 --network none \
            --read-only --cap-drop ALL \
            --security-opt no-new-privileges:true \
            --mount type=bind,src=/run,dst=/host-run \
            --entrypoint /bin/sh "$SOURCE_IMAGE" \
            -ec 'rmdir /host-run/cwa-translate-btctl-locks' \
            >/dev/null 2>&1 || true
    fi
    if [ "$cwa_image_created" -eq 1 ]; then
        docker image rm "$CWA_IMAGE" >/dev/null 2>&1 || true
    fi
    if [ "$source_image_created" -eq 1 ]; then
        docker image rm "$SOURCE_IMAGE" >/dev/null 2>&1 || true
    fi
    if [ "$image_created" -eq 1 ]; then
        docker image rm "$DISPATCHER_IMAGE" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$TEMPORARY"
    if [ "$status" -eq 0 ] && [ "$cleanup_failed" -ne 0 ]; then
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

test -d /mnt
test -z "$(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all)"

docker build --pull=false \
    --target dispatcher-smoke \
    --build-arg "BTCTL_SOURCE_REVISION=$REVISION" \
    --build-arg "BTCTL_SOURCE_VERSION=$VERSION" \
    --tag "$DISPATCHER_IMAGE" \
    --file "$ROOT_DIR/Dockerfile.btctl" \
    "$ROOT_DIR" >/dev/null
image_created=1

docker build --pull=false \
    --target source-exporter \
    --tag "$SOURCE_IMAGE" \
    --file "$ROOT_DIR/Dockerfile.btctl" \
    "$ROOT_DIR" >/dev/null
source_image_created=1

if [ ! -e "$HOST_LOCK_DIRECTORY" ]; then
    docker run --rm --user 0:0 --network none \
        --read-only --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --mount type=bind,src=/run,dst=/host-run \
        --entrypoint /bin/sh "$SOURCE_IMAGE" \
        -ec 'mkdir -m 0700 /host-run/cwa-translate-btctl-locks'
    lock_directory_created=1
fi
test -d "$HOST_LOCK_DIRECTORY"
test ! -L "$HOST_LOCK_DIRECTORY"
test "$(stat -c '%u:%a' "$HOST_LOCK_DIRECTORY")" = "0:700"

test ! -e "$TEMPLATE_ROOT/my-cwa-translate-api.xml"
test ! -e "$TEMPLATE_ROOT/my-cwa-translate-proxy.xml"
template_files_reserved=1
if [ ! -d "$TEMPLATE_ROOT" ]; then
    template_root_created=1
fi
if [ ! -d /boot/config/plugins/dockerMan ]; then
    template_dockerman_created=1
fi
if [ ! -d /boot/config/plugins ]; then
    template_plugins_created=1
fi
if [ ! -d /boot/config ]; then
    template_config_created=1
fi
docker run --rm --user 0:0 --network none \
    --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount type=bind,src=/boot,dst=/host-boot \
    --entrypoint /bin/sh "$SOURCE_IMAGE" \
    -ec 'mkdir -p -m 0755 \
        /host-boot/config/plugins/dockerMan/templates-user'

docker run --rm --user 0:0 --network none \
    --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount type=bind,src=/mnt,dst=/host-mnt \
    --entrypoint /bin/sh "$SOURCE_IMAGE" \
    -ec 'mkdir -m 0700 "/host-mnt/$1"' sh "$SHARE_ROOT"
share_created=1

cat >"$CWA_FIXTURE" <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth_path = self.path == "/ajax/emailstat"
        authenticated = "session=valid" in self.headers.get("Cookie", "")
        if auth_path and not authenticated:
            body = b'{"error":"unauthorized"}'
            status = 401
        else:
            body = b"[]" if auth_path else b"ok"
            status = 200
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json" if auth_path else "text/plain",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


HTTPServer(("0.0.0.0", 8083), Handler).serve_forever()
PY
chmod 0644 "$CWA_FIXTURE"
docker tag "$SOURCE_IMAGE" "$CWA_IMAGE"
cwa_image_created=1
docker network create "$CWA_NETWORK" >/dev/null
docker run -d --name "$CWA_CONTAINER" --network "$CWA_NETWORK" \
    --read-only --tmpfs /tmp \
    --mount "type=bind,src=$CWA_FIXTURE,dst=/fixture.py,readonly" \
    --entrypoint python3 "$CWA_IMAGE" /fixture.py >/dev/null
for _ in $(seq 1 30); do
    if docker exec "$CWA_CONTAINER" python3 -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8083/", timeout=1).close()' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$CWA_CONTAINER" python3 -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8083/", timeout=1).close()'

{
    printf 'BT_INSTALL_PROFILE=unraid\n'
    printf 'BT_INSTALL_NAME=%s\n' "$INSTALL_NAME"
    printf 'BT_INGRESS_MODE=published\n'
    printf 'BT_PROXY_PORT=%s\n' "$PROXY_PORT"
    printf 'BT_EDGE_NETWORK=\n'
    printf 'BT_AUTH_PROFILE=cwa-session\n'
    printf 'BT_PUBLIC_ORIGIN=http://127.0.0.1:%s\n' "$PROXY_PORT"
    printf 'CWA_UPSTREAM=http://%s:8083\n' "$CWA_CONTAINER"
    printf 'BT_CWA_CONTAINER=%s\n' "$CWA_CONTAINER"
    printf 'BT_CWA_NETWORK=%s\n' "$CWA_NETWORK"
    printf 'BT_CWA_VERSION=4.0.6\n'
    printf 'BT_STATE_DIR=/mnt/%s/appdata/state\n' "$SHARE_ROOT"
    printf 'BT_DATA_DIR=/mnt/%s/appdata/data\n' "$SHARE_ROOT"
    printf 'BT_BACKUP_DIR=/mnt/%s/backups\n' "$SHARE_ROOT"
    printf 'BT_UNRAID_TEMPLATE_DIR=/boot/config/plugins/dockerMan/templates-user\n'
    printf 'LLM_PROVIDER=local\n'
    printf 'LLM_MODEL=smoke-model\n'
    printf 'BT_LOCAL_URL=http://192.0.2.10:2819/v1/chat/completions\n'
    printf 'LLM_API_KEY=\n'
} >"$ENV_FILE"
chmod 0600 "$ENV_FILE"

outer_mounts=(
    --mount "type=bind,src=$ROOT_DIR,dst=$ROOT_DIR,readonly"
    --mount "type=bind,src=$ENV_FILE,dst=$ENV_FILE,readonly"
    --mount type=bind,src=/mnt,dst=/mnt,readonly
    --mount type=bind,src=/boot,dst=/boot,readonly
    --mount "type=bind,src=$HOST_LOCK_DIRECTORY,dst=$HOST_LOCK_DIRECTORY,readonly"
    --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock
)

run_without_host_tooling() {
    docker run --rm --user 0:0 --network none \
        --read-only --pids-limit 128 --cap-drop ALL \
        --cap-add DAC_READ_SEARCH \
        --security-opt no-new-privileges:true \
        --env HOME=/tmp/home \
        --env DOCKER_CONFIG=/tmp/docker \
        --env XDG_CONFIG_HOME=/tmp/config \
        --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
        "${outer_mounts[@]}" \
        --entrypoint /bin/bash "$DISPATCHER_IMAGE" \
        -ec '
            if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
                echo "dispatcher smoke unexpectedly has host Python" >&2
                exit 1
            fi
            if command -v git >/dev/null 2>&1; then
                echo "dispatcher smoke unexpectedly has host Git" >&2
                exit 1
            fi
            exec "$@"
        ' bash "$ROOT_DIR/btctl" "$@"
}

run_without_host_tooling plan --env "$ENV_FILE" --json >"$OUTPUT"

python3 - "$OUTPUT" "$REVISION" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
revision = sys.argv[2]
assert payload["revision"] == revision, payload
assert payload["image"].endswith(revision[:12]), payload
assert payload["install_profile"] == "unraid", payload
PY

run_without_host_tooling \
    install --env "$ENV_FILE" --yes --json >"$TEMPORARY/install.json"
run_without_host_tooling \
    doctor --env "$ENV_FILE" --json >"$TEMPORARY/doctor.json"

python3 - "$TEMPORARY/install.json" "$TEMPORARY/doctor.json" "$REVISION" <<'PY'
import json
import sys
from pathlib import Path

installed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
doctor = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
revision = sys.argv[3]
assert installed["status"] == "installed", installed
assert installed["revision"] == revision, installed
assert doctor["ok"] is True, doctor
assert all(check["status"] == "ok" for check in doctor["checks"]), doctor
PY

run_without_host_tooling \
    uninstall --env "$ENV_FILE" --yes --json >"$TEMPORARY/uninstall.json"

python3 - "$TEMPORARY/uninstall.json" <<'PY'
import json
import sys
from pathlib import Path

uninstalled = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert uninstalled["status"] == "uninstalled", uninstalled
PY

docker inspect "$CWA_CONTAINER" >/dev/null
if docker inspect "${INSTALL_NAME}-api" >/dev/null 2>&1 \
    || docker inspect "${INSTALL_NAME}-proxy" >/dev/null 2>&1 \
    || docker network inspect "${INSTALL_NAME}-private" >/dev/null 2>&1; then
    echo "bootstrap uninstall left managed runtime resources" >&2
    exit 1
fi
docker run --rm --user 0:0 --network none \
    --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount type=bind,src=/mnt,dst=/host-mnt,readonly \
    --mount type=bind,src=/boot,dst=/host-boot,readonly \
    --entrypoint /bin/sh "$SOURCE_IMAGE" \
    -ec '
        test -d "/host-mnt/$1/appdata/data"
        test -f "/host-mnt/$1/appdata/state/state.json"
        test ! -e /host-boot/config/plugins/dockerMan/templates-user/my-cwa-translate-api.xml
        test ! -e /host-boot/config/plugins/dockerMan/templates-user/my-cwa-translate-proxy.xml
    ' sh "$SHARE_ROOT"

printf 'btctl stock-Unraid plan/install/doctor/uninstall without host Python: OK\n'
