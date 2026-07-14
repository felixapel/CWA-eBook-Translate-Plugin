# Managed Unraid deployment

The supported v2.2.0 Unraid path is local and source-only. It does not SSH into
the server, download a project image, publish the API, copy a CWA template, or
mount an overlay into CWA. One clean checkout builds one immutable local image;
that image runs as two separate non-root containers.

```text
browser/reverse proxy -> cwa-translate-proxy -> stock CWA
                                  |
                                  +-> cwa-translate-api -> local/cloud LLM
                                               |
                                               +-> private appdata cache
```

## What you need

- Run these commands in a clean release checkout on the Unraid host.
- Run as `root`; the API image writes as uid `101`, gid `102` and `btctl`
  creates its appdata directory with those exact owners.
- Use an exact CWA image tag such as
  `crocodilestick/calibre-web-automated:v4.0.6`. A mutable `latest` CWA tag is
  not enough evidence for compatibility and installation stops before build.
- CWA `4.x` is Tier 1. Exactly `3.1.4` is accepted only by the v2.1.4 migration
  workflow.
- Know the running CWA container name and one Docker network it already joins.
- For a local provider, expose an OpenAI-compatible
  `/v1/chat/completions` endpoint to Docker. Do not use `localhost` unless the
  LLM really runs inside the translator API container.

## Configure

Keep configuration, deployment state, translation data, and backups outside
the Git checkout:

```bash
install -d -m 0700 /mnt/user/appdata/cwa-translate
cp .env.example /mnt/user/appdata/cwa-translate/install.env
chmod 0600 /mnt/user/appdata/cwa-translate/install.env
```

Edit the copy. A typical local-provider configuration has:

```dotenv
BT_INSTALL_PROFILE=unraid
BT_INSTALL_NAME=cwa-translate
BT_INGRESS_MODE=published
BT_PROXY_PORT=8385
BT_EDGE_NETWORK=
BT_AUTH_PROFILE=cwa-session
BT_PUBLIC_ORIGIN=https://books.example.com
CWA_UPSTREAM=http://calibre-web-automated:8083
BT_CWA_CONTAINER=calibre-web-automated
BT_CWA_NETWORK=cwa_default
BT_CWA_VERSION=4.0.6
BT_STATE_DIR=/mnt/user/appdata/cwa-translate/state
BT_DATA_DIR=/mnt/user/appdata/cwa-translate/data
BT_BACKUP_DIR=/mnt/user/backups/cwa-translate
BT_UNRAID_TEMPLATE_DIR=/boot/config/plugins/dockerMan/templates-user
LLM_PROVIDER=local
LLM_MODEL=gemma4-12b
BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions
LLM_API_KEY=
```

The project is open source, so no project key or registry credential exists.
`LLM_API_KEY` is only for a cloud model provider; it must stay empty when
`LLM_PROVIDER=local`.

## Authentication profiles

`cwa-session` is the safe default. The browser sends its native CWA session to
the same-origin proxy, and the API validates selected cookies against CWA's
exact `/ajax/emailstat` endpoint. An Authentik cookie by itself is not a CWA
session. If CWA is configured for OIDC and ultimately creates a native CWA
session, the normal profile works.

`authentik-forwarded` is an advanced separate topology. It requires
`docker-edge`, no host port, an exact identity-proxy `/32` or `/128`, and a
patched Authentik version. Follow [AUTHENTIK.md](AUTHENTIK.md); merely enabling
forwarded headers on the browser-facing injection proxy is intentionally not
supported.

`disabled`, a shared browser token, broad trusted CIDRs, and an API host port
are not managed installation profiles.

## Plan and install

First validate without changing the filesystem or Docker:

```bash
./btctl plan --env /mnt/user/appdata/cwa-translate/install.env
```

Review the image name, source SHA, CWA evidence, networks, ports, paths, and
ownership. Then run either command:

```bash
./btctl install \
  --env /mnt/user/appdata/cwa-translate/install.env --yes

# equivalent root-only convenience wrapper
./install_unraid.sh /mnt/user/appdata/cwa-translate/install.env
```

Docker may fetch the digest-pinned public Python/Alpine base image during the
local build. It never pulls a CWA Translate project image. The resulting image
name is `local/cwa-translate:<version>-<sha12>` and both roles must resolve to
the same full image ID.

The API has no `PortBindings`. In `published` mode only the proxy maps
`BT_PROXY_PORT` to container port `8080`. In `docker-edge` mode neither role is
published. The private network uses no fixed subnet; Docker chooses a
non-conflicting range.

## Docker tab and generated templates

After every live postcondition passes, `btctl` writes
`my-cwa-translate-api.xml` and `my-cwa-translate-proxy.xml` into DockerMan's
user-template directory. They contain the immutable image and reference a
private `api.env`/`proxy.env`; no LLM key is copied into XML.

The live containers use an additional external network that DockerMan's
single-network form cannot represent. The template overview therefore says
not to press **Apply**. Use `btctl` for lifecycle changes; DockerMan remains
useful for status, logs, and start/stop visibility.

## State recovery

If only `state.json` was lost but both containers, their private network,
image, templates, environment, health, ports, networks, install ID, version,
and revision still match, recover state with:

```bash
./btctl adopt --env /mnt/user/appdata/cwa-translate/install.env
```

Adoption performs no Docker mutation. Any ambiguity or insecure drift stops it.
It does not adopt arbitrary manual containers and does not reinterpret a
combined v2.1.4 container as v2.2.0.

## Existing v2.1.4 data

Do not point a normal install at the live v2.1.4 appdata directory. The legacy
container is the only writer and must be stopped before a snapshot. Migration
backups belong outside appdata under the configured directory, normally
`/mnt/user/backups/cwa-translate/`. The migration gate performs
`PRAGMA wal_checkpoint(TRUNCATE)` while the source is controlled, copies the
offline tree into a new empty target, runs `PRAGMA integrity_check`, and keeps
the exact old image/container stopped and restartable. It does not use SQLite's
immutable read-only URI flag, because that could ignore required WAL data.
Until `btctl upgrade` reports a completed journal, use the audited manual
procedure in [RELEASE.md](RELEASE.md) rather than the normal install command.

## Failure behavior

Name/template collisions, missing networks, stopped or unversioned CWA, and
invalid configuration stop before the image build. A later startup failure
removes only the newly created proxy, API, and private network, in that order.
CWA, external networks, appdata, environment files, and backups are preserved;
`state.json` is not written.
