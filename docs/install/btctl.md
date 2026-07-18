# Managed `btctl` installation

`btctl` is the lifecycle authority for the recommended split deployment. It
builds one immutable local image from an exact clean checkout, runs separate
non-root proxy and API roles, and never edits or owns the stock CWA container.

```text
browser / reverse proxy -> cwa-translate-proxy -> stock CWA
                                   |
                                   +-> cwa-translate-api -> LLM
                                                |
                                                +-> private cache
```

## Choose a profile

| Profile | Operator | Requirements |
|---|---|---|
| `unraid` | root | Stock Unraid, Bash, local Docker socket, full Git checkout; host Python, host Git and NerdTools are not required to run `./btctl`. |
| `compose-existing` | trusted Docker-capable account | Linux, Docker Engine plus Compose plugin, and the same private primary group/account for every lifecycle command. |

Both profiles require a running CWA container on a named Docker network, an
exact stable CWA version, private state/data/backup paths outside this checkout
and an LLM reachable from Docker. CWA 4.x is the fresh-install tier. Exactly CWA
3.1.4 is accepted only as the source of the v2.1.4 migration.

Keep the complete checkout including `.git` and select an annotated release
tag. A forge ZIP or tarball cannot prove source identity and is unsupported.
The launcher is a root trust boundary on Unraid; obtain it and the checkout from
the same reviewed release.

## Configure

Create a private copy of the example outside the repository:

```bash
install -d -m 0700 /absolute/private/path
cp .env.example /absolute/private/path/cwa-translate.env
chmod 0600 /absolute/private/path/cwa-translate.env
```

At minimum, set:

```dotenv
BT_INSTALL_PROFILE=unraid
BT_INSTALL_NAME=cwa-translate
BT_INGRESS_MODE=published
BT_PROXY_PORT=8385
BT_AUTH_PROFILE=cwa-session
BT_PUBLIC_ORIGIN=https://books.example.com
CWA_UPSTREAM=http://calibre-web-automated:8083
BT_CWA_CONTAINER=calibre-web-automated
BT_CWA_NETWORK=cwa_default
BT_CWA_VERSION=4.0.6
BT_CWA_IDENTITY_HEADER=Remote-User
BT_STATE_DIR=/absolute/private/path/state
BT_DATA_DIR=/absolute/private/path/data
BT_BACKUP_DIR=/absolute/private/path/backups
LLM_PROVIDER=local
LLM_MODEL=gemma4-12b
BT_LOCAL_URL=http://192.168.1.50:8000/v1/chat/completions
LLM_API_KEY=
```

For Compose, change `BT_INSTALL_PROFILE=compose-existing` and use paths writable
through the trusted operator's private group. For Unraid, use existing
`/mnt/user/<share>` or `/mnt/<pool>` roots and set
`BT_UNRAID_TEMPLATE_DIR=/boot/config/plugins/dockerMan/templates-user`.
`btctl` refuses misspelled share/pool roots.

`CWA_UPSTREAM` must be exactly `http://<BT_CWA_CONTAINER>:8083`.
`BT_CWA_IDENTITY_HEADER` must match CWA's configured reverse-proxy login header;
the managed proxy strips client-supplied copies before forwarding to CWA. A
local provider normally leaves `LLM_API_KEY` empty.

## Authentication and ingress

`cwa-session` is the default. It validates selected native CWA cookies against
the exact authenticated JSON endpoint and supports `config_session=1` by
preserving the browser User-Agent and the address observed by the managed
proxy. Keep CWA's default `TRUSTED_PROXY_COUNT=1` and do not create a bypass to
the API.

`authentik-forwarded` is a separate advanced topology. It requires
`docker-edge`, an exact trusted identity peer and the generated direct API edge
route. Follow the [Authentik guide](authentik.md). Disabled authentication,
browser shared tokens, broad trusted CIDRs and a published API are not managed
fallbacks.

In `published` mode, only the proxy maps a host port. In `docker-edge`, neither
role is published and the configured external edge network must already exist.
The private API network uses no fixed subnet.

## Plan, install and verify

Validate before changing deployment state:

```bash
./btctl plan --env /absolute/private/path/cwa-translate.env
```

Review source/image identity, CWA evidence, roles, ports, networks, paths and
ownership. Then install and verify:

```bash
./btctl install --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

On Unraid, `./install_unraid.sh /absolute/private/path/cwa-translate.env` is an
equivalent root-only convenience wrapper.

On a host without Python 3.11 or Git, the Bash launcher builds a short-lived,
socket-free source exporter and a separate operator image containing Python,
Git and the Docker CLI. The exporter receives the checkout read-only, disables
Git replacement refs, verifies the clean commit and streams its archive. Only
the verified operator receives command-specific mounts and, when required, the
local Docker socket. `plan` and `auth-snippet` receive no socket. The bootstrap
may fetch pinned base images and warm ordinary build cache, but `plan` creates
no deployment state or runtime resources.

The Docker socket is equivalent to root. The Unraid fallback is root-only,
forces `/var/run/docker.sock`, ignores remote Docker contexts and never mounts
the socket into the exporter. The production image is separate and runs both
long-lived roles as uid `101`, gid `102` with a read-only root filesystem and no
capabilities.

## Generated artifacts

The Compose profile writes a private mode-`0600` Compose JSON document and
state. The Unraid profile writes API/proxy DockerMan templates that reference
private role environment files and the immutable local image. The additional
network cannot be represented safely in DockerMan's single-network form, so do
not press **Apply** on generated templates; use `btctl` for lifecycle changes.

## Acceptance

- `doctor` succeeds with every check `ok`.
- The API has no host `PortBindings`; only the proxy is published in
  `published` mode and neither role is published in `docker-edge`.
- Direct CWA and OPDS/Kobo routes continue to work.
- Through `BT_PUBLIC_ORIGIN`, sign in, open a DRM-free EPUB, translate with
  different source and target languages, change page and reload.
- The toolbar survives reload and browser storage exposes no translator token
  or provider key.
- Authentik installations also complete the guide's public-path checks.

For `doctor`, `adopt`, migration, rollback, uninstall and failure recovery, use
the [lifecycle guide](../operations/lifecycle.md). Do not recover by exposing
the API, disabling auth, editing state or applying generated templates.
