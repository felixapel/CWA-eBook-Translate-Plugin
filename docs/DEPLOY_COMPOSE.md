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
- The account running `btctl` must be allowed to use Docker and create the
  configured storage paths. Root is not required. Use the same account for
  later install, upgrade, rollback, doctor, and uninstall commands, and ensure
  its primary group is private to trusted operators. After the image is built,
  a one-shot local container gives runtime uid `101` ownership while retaining
  setgid read access for that operator group; no manual numeric `chown` is
  required.

## Install

Copy the example outside the repository and set at least the origin, exact CWA
container/network/version, matching CWA identity header, storage paths, and LLM
provider. `CWA_UPSTREAM` must be exactly
`http://<BT_CWA_CONTAINER>:8083`; arbitrary aliases and IPs are rejected:

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
image and never uses `latest`; it feeds Docker a `git archive` of the recorded
commit rather than the mutable working directory. Docker may fetch the
digest-pinned public base image if it is not already local. The generated private Compose document is
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

## v2.1.4 upgrade and rollback

The managed upgrade is only for one exact combined v2.1.4 container. Keep its
data path separate from the new `BT_DATA_DIR`, set `BT_LEGACY_CONTAINER` and
`BT_LEGACY_DATA_DIR`, and run:

```bash
./btctl upgrade --env /srv/cwa-translate/install.env --yes
./btctl doctor --env /srv/cwa-translate/install.env
```

The migration stops the legacy writer before its WAL checkpoint and snapshot,
validates SQLite before and after copying, then installs the split target. It
probes `/app/VERSION` inside the immutable old image ID, preserves the exact old
container and image for rollback, and journals atomic work directories. After
the exact legacy container is stopped, a constrained one-shot container
preserves its owner and grants the invoking operator group only the access
needed for the checkpoint and copy. A retry after `prepared`,
`snapshot-complete`, or a failed re-upgrade first checks for an already-live
cutover. A complete exact v2.2 runtime is adopted and journaled in place; if no
v2.2 resources exist, interrupted copies are preserved and a new numbered
attempt begins. Partial or mismatched resources fail before the target bind is
moved. If browser acceptance fails:

```bash
./btctl rollback --env /srv/cwa-translate/install.env --yes
```

Do not point a normal install at the legacy data path and do not start both
versions against the same database.

Rollback prioritizes restoring and health-checking the exact v2.1.4 container.
If the preserved v2.2 data tree is missing, unreadable, or corrupt, rollback
still completes but records that target as unavailable; automatic re-upgrade is
then blocked until the target is explicitly repaired or restored.

## Verify, diagnose, and remove

Run the read-only verifier after installation, Docker/host restarts, and any
migration:

```bash
./btctl doctor --env /srv/cwa-translate/install.env
```

It checks state, ownership, source/image identity, CWA evidence, health,
environment, authentication, networks, ports, and private artifacts. Resolve
every failed check before using the deployment.

Remove only the runtime owned by this install with:

```bash
./btctl uninstall --env /srv/cwa-translate/install.env --yes
```

The retryable operation preserves CWA, external networks, the local image,
translation data, backups, and state evidence.

Reinstalling the same runtime identity and data path after a completed
`uninstall` is supported. On success, the old final state is first archived as
`BT_STATE_DIR/history/<install-id>-uninstalled.json`; no active or ambiguous
state can be overwritten.

## Acceptance checklist

- `./btctl doctor --env ...` succeeds with every check `ok`.
- The API service has no host port. Only the injection proxy is published in
  `published` mode; neither role is published in `docker-edge` mode.
- Direct CWA access and any OPDS/Kobo routes continue to work.
- Through `BT_PUBLIC_ORIGIN`, sign in, open a DRM-free EPUB, choose different
  source and target languages, translate, change page, and reload.
- Browser storage and generated Compose/state files expose neither an LLM key
  nor a translator browser token; private generated artifacts remain mode
  `0600`.
- `authentik-forwarded` deployments also pass the edge-specific checklist in
  [AUTHENTIK.md](AUTHENTIK.md).

## Failure behavior

Every external dependency and name collision is checked before the image build
or runtime creation. If startup or post-start verification fails, Compose is
brought down using the generated two-service document. Translation data is
never removed, and no successful state is written. Keep the private document
for diagnosis, correct the cause, and rerun only after checking no unexpected
containers or network remain.
