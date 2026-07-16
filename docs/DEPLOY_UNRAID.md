# Managed Unraid deployment

The advanced v2.2 Unraid path is local and source-built. It does not SSH into
the server, download a project image, publish the API, copy a CWA template, or
mount an overlay into CWA. One clean checkout builds one immutable local image;
that image runs as two separate non-root containers.

For the simpler single-container Community Applications profile introduced in
v2.2.1, use [DEPLOY_UNRAID_CA.md](DEPLOY_UNRAID_CA.md). This guide remains the
recommended path for upgrades, rollback, Authentik, and independent roles.

```text
browser/reverse proxy -> cwa-translate-proxy -> stock CWA
                                  |
                                  +-> cwa-translate-api -> local/cloud LLM
                                               |
                                               +-> private appdata cache
```

## What you need

- Keep a full Git checkout, including its `.git` directory, on the Unraid host
  and run commands from that exact clean release commit. A release ZIP or
  tarball by itself cannot prove the commit identity and is not supported.
- Stock Unraid needs Bash and a working Docker daemon. It does not require host Python or NerdTools.
  Host Git is not required to execute `./btctl`; if it is
  absent, Claude Code or another Git-capable machine may prepare the exact
  checkout and copy the complete directory, including `.git`, to Unraid.
- Obtain the checkout and its public `btctl` launcher from the trusted Gitea
  repository/commit. The launcher plus its embedded pinned exporter definition
  are the bootstrap trust root; no self-check can make an arbitrary malicious
  root script safe to execute.
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

`/mnt/user/appdata` and `/mnt/user/backups` must already exist as Unraid user
shares. For a named pool, `/mnt/<pool>` must already exist. `btctl` refuses a
misspelled `/mnt/user/<share>` instead of creating an unintended share.

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
BT_CWA_IDENTITY_HEADER=Remote-User
BT_STATE_DIR=/mnt/user/appdata/cwa-translate/state
BT_DATA_DIR=/mnt/user/appdata/cwa-translate/data
BT_BACKUP_DIR=/mnt/user/backups/cwa-translate
BT_UNRAID_TEMPLATE_DIR=/boot/config/plugins/dockerMan/templates-user
LLM_PROVIDER=local
LLM_MODEL=gemma4-12b
BT_LOCAL_URL=http://192.168.1.50:8000/v1/chat/completions
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
session, the normal profile works. CWA `config_session=1` is supported: the
managed proxy sends the same exact browser `User-Agent` and observed peer on
the login and API paths, while the API replays that context during validation.
`btctl` configures the proxy's private Docker alias as the sole authority for
that address. Keep CWA's default `TRUSTED_PROXY_COUNT=1`; a custom hop count
changes the address CWA binds into its session and is outside the certified
topology. Do not publish the API port or insert a different route that bypasses
the generated injection proxy.

`authentik-forwarded` is an advanced separate topology. It requires
`docker-edge`, no host port, an exact identity-proxy `/32` or `/128`, and a
patched Authentik version. Follow [AUTHENTIK.md](AUTHENTIK.md); merely enabling
forwarded headers on the browser-facing injection proxy is intentionally not
supported.

`disabled`, a shared browser token, broad trusted CIDRs, and an API host port
are not managed installation profiles.

`CWA_UPSTREAM` is not an independent trust choice: it must be exactly
`http://<BT_CWA_CONTAINER>:8083`. Set `BT_CWA_IDENTITY_HEADER` to the exact
reverse-proxy login header configured in CWA (for example `Remote-User` or
`X-Forwarded-User`); the managed proxy validates and strips that client-supplied
credential before forwarding to CWA.

## Plan and install

First validate without changing deployment state, data, CWA, or running
containers:

```bash
./btctl plan --env /mnt/user/appdata/cwa-translate/install.env
```

Review the image name, source SHA, CWA evidence, networks, ports, paths, and
ownership. Then run either command:

```bash
./btctl install --env /mnt/user/appdata/cwa-translate/install.env --yes

# equivalent root-only convenience wrapper
./install_unraid.sh /mnt/user/appdata/cwa-translate/install.env
```

On a host without Python 3.11+, `./btctl` automatically builds a short-lived
source exporter from the launcher's embedded pinned definition and a separate
operator image containing Python, Git, and the Docker CLI. No unverified
checkout Dockerfile is used to create the exporter. The exporter receives the
checkout read-only and never receives the Docker socket. It disables Git
replacement refs, verifies the clean full commit, and streams a Git archive
into the exact operator build. Only that verified operator receives the
command-specific paths and, when required, the Docker socket. `plan` and
`auth-snippet` receive no socket.

This bootstrap may fetch the digest-pinned public Python/Alpine base image,
creates and removes temporary helper images, and can warm Docker's build cache,
including during the first `plan`. It never pulls a CWA Translate project image
and does not change deployment files or runtime resources during `plan`. The
production image remains separate and runs both roles as uid `101`, gid `102`;
it contains none of the operator's Git or Docker administration tools. Its name
is `local/cwa-translate:<version>-<sha12>` and both roles must resolve to the
same full image ID.

If Python 3.11+ exists but host Git does not, the same fallback is selected;
installing an unrelated Python plugin cannot make the documented path depend
on Git accidentally. Lifecycle commands share an empty root-owned lock under
`/run/cwa-translate-btctl-locks`, preventing concurrent native/containerized
mutations without mounting the surrounding appdata into the operator.

The Docker socket is equivalent to root access. For that reason the fallback
launcher is root-only, mounts only the paths required by the selected command,
forces the local Unix socket at `/var/run/docker.sock` instead of honoring a
remote Docker context, and never exposes the socket to the source exporter or
to socket-free commands.

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
Set both legacy fields to the exact stopped-or-running v2.1.4 container and its
bind-mounted data directory, then let the managed migration perform the stop,
checkpoint, integrity checks, external snapshot, target copy, and cutover:

```dotenv
BT_LEGACY_CONTAINER=book-translator-v214-rollback
BT_LEGACY_DATA_DIR=/mnt/user/appdata/book-translator
```

```bash
./btctl upgrade --env /mnt/user/appdata/cwa-translate/install.env --yes
./btctl doctor --env /mnt/user/appdata/cwa-translate/install.env
```

Do not run normal `install` against live v2.1.4 data. If acceptance fails, the
journal binds rollback to the exact legacy container, image, source manifest,
and snapshot:

```bash
./btctl rollback --env /mnt/user/appdata/cwa-translate/install.env --yes
```

The two `BT_LEGACY_*` values are upgrade inputs. Rollback reads the authoritative
legacy data path from the private migration journal, so stale or removed legacy
path fields in the environment cannot redirect restoration.

The manual sequence in [RELEASE.md](RELEASE.md) remains historical/operator
reference; `btctl upgrade` is the supported v2.1.4 path.

## Verify, diagnose, and remove

Run the read-only verifier after installation, after an Unraid reboot, and
before declaring an upgrade successful:

```bash
./btctl doctor --env /mnt/user/appdata/cwa-translate/install.env
```

It validates the private state, source/image identity, CWA evidence, role
labels, health, environment, authentication contract, networks, published
ports, and generated artifacts. A failed or missing check is a failed
deployment; do not work around it by exposing the API or disabling auth.

To remove only resources owned by this install:

```bash
./btctl uninstall --env /mnt/user/appdata/cwa-translate/install.env --yes
```

The operation is retryable. It preserves CWA, external networks, translation
data, backups, the local image, and state evidence.

After a completed `uninstall`, the same runtime identity and data path may be
installed again. The new successful install archives the prior final state as
`BT_STATE_DIR/history/<install-id>-uninstalled.json` before replacing
`state.json`; active, partial, rolled-back, or mismatched state is still
rejected.

## Acceptance checklist

- `./btctl doctor --env ...` reports success with every check `ok`.
- `cwa-translate-api` has no host `PortBindings`; only the proxy is published
  in `published` mode, and neither role is published in `docker-edge` mode.
- The normal CWA URL still works directly, including OPDS/Kobo if used.
- Through `BT_PUBLIC_ORIGIN`, sign in, open a DRM-free EPUB, choose different
  source and target languages, translate a paragraph, change page, and reload.
- The toolbar remains present after reload and a repeated paragraph can use the
  private cache. Browser DevTools shows no translator token or LLM key.
- For `authentik-forwarded`, complete the additional public-path acceptance in
  [AUTHENTIK.md](AUTHENTIK.md); a direct request to the API remains impossible.

## Failure behavior

An invalid checkout stops before any operator gets the Docker socket. Because
the stock-host fallback must first create the verified parser environment,
invalid deployment configuration can still leave ordinary Docker build cache;
it does not create deployment state or runtime resources. Name/template
collisions, missing networks, and stopped or unversioned CWA stop before the
production image build. A later startup failure removes only the newly created
proxy, API, and private network, in that order. CWA, external networks, appdata,
environment files, and backups are preserved; `state.json` is not written.
