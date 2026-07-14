# Managed Compose deployment

This is the supported path when CWA already runs under Docker Compose. `btctl`
does not edit, recreate, stop, or take ownership of CWA. It builds the exact
checked-out translator source and manages only one API container, one injection
proxy container, and their private network.

## Prerequisites

- Docker Engine with the `docker compose` plugin.
- A running CWA container on one explicitly named Docker network.
- CWA `4.x` for Tier 1 support. Exactly `3.1.4` is accepted only as a legacy
  migration source.
- An absolute private state directory, data directory, and backup directory
  outside this checkout.
- The account running `btctl` must be allowed to use Docker. The bind-mounted
  data directory must be writable by image uid `101`, gid `102`; when needed,
  create it with `sudo install -d -o 101 -g 102 -m 0700 <path>`.

## Install

Copy the example outside the repository and set at least the origin, exact CWA
container/network/version, storage paths, and LLM provider:

```bash
cp .env.example /srv/cwa-translate/install.env
chmod 600 /srv/cwa-translate/install.env
```

Use `BT_INSTALL_PROFILE=compose-existing`. `BT_INSTALL_NAME` becomes the stable
container/project prefix, so choose it once. Review the mutation-free plan:

```bash
./btctl plan --env /srv/cwa-translate/install.env
```

Then install exactly that clean checkout:

```bash
./btctl install --env /srv/cwa-translate/install.env --yes
```

The local image is named
`local/cwa-translate:<VERSION>-<full-SHA-prefix>`. `btctl` never pulls a project
image and never uses `latest`; Docker may fetch the digest-pinned public base
image if it is not already local. The generated private Compose document is
mode `0600` because it contains the API runtime environment. `state.json` is
also `0600` but contains no API key.

With `BT_INGRESS_MODE=published`, only `<BT_PROXY_PORT>:8080` is published. The
API has no `ports` entry. With `docker-edge`, neither role has a host binding;
the configured edge network is external and must already exist.

## State recovery (`adopt`)

`adopt` is deliberately narrow. It recovers a lost `state.json` only when both
split containers and their private network already carry matching `btctl`
install-id, role, version, revision, image-ID, network, health, and port
evidence:

```bash
./btctl adopt --env /srv/cwa-translate/install.env
```

It does not change Docker. Unlabeled or partially matching containers are
rejected. A combined v2.1.4 container is routed to the explicit migration path
instead of being relabeled or recreated.

## Failure behavior

Every external dependency and name collision is checked before the image build
or runtime creation. If startup or post-start verification fails, Compose is
brought down using the generated two-service document. Translation data is
never removed, and no successful state is written. Keep the private document
for diagnosis, correct the cause, and rerun only after checking no unexpected
containers or network remain.
