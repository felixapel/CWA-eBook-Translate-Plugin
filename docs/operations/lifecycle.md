# Managed lifecycle and recovery

Use the same private environment file and the same operator account for every
operation. The Unraid profile runs as root; `compose-existing` may use a trusted
Docker-capable account with a private primary group.

## Verify

Run the read-only verifier after install, host or Docker restart, upgrade and
rollback:

```bash
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

It checks source/image identity, private state, CWA evidence, role labels,
health, authentication, networks, ports and generated artifacts. Treat any
failed or missing check as a failed deployment.

## Recover lost state

If only `state.json` was lost, `adopt` can reconstruct it from an exact healthy,
already-labelled split runtime:

```bash
./btctl adopt --env /absolute/private/path/cwa-translate.env
```

Adoption does not change Docker. It rejects unlabeled, partial, insecure or
ambiguous containers and routes a combined legacy container to the migration
workflow instead of relabeling it.

## Upgrade from the combined v2.1.4 runtime

Migration supports one exact v2.1.4 container. Keep its live data separate from
the new `BT_DATA_DIR`, and set:

```dotenv
BT_LEGACY_CONTAINER=book-translator-v214-rollback
BT_LEGACY_DATA_DIR=/absolute/path/to/legacy-data
```

A source-built legacy container must use the `2.1.4` or `v2.1.4` image tag.
For compatibility with the historical Community Applications template, the
exact image reference
`ghcr.io/felixapel/cwa-ebook-translate-plugin:latest` is also recognized, but
only when a network-disabled, read-only probe of the container's immutable
image ID reports `/app/VERSION` as `2.1.4`. The verified image ID is recorded in
the migration journal. `btctl` does not pull or resolve the mutable tag during
this check, and every other `latest` reference fails closed.

Then run:

```bash
./btctl upgrade --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

The upgrade stops the only writer, checkpoints the SQLite WAL, validates the
source, creates an external snapshot, copies into a new empty target, validates
the copy and keeps the exact legacy container/image restartable. The private
journal makes interrupted preparation, snapshot and cutover steps retryable.
Never point a fresh install at the live legacy directory or run both versions
against one database.

## Roll back a migration

If browser acceptance fails:

```bash
./btctl rollback --env /absolute/private/path/cwa-translate.env --yes
```

Rollback restores and health-checks the exact preserved legacy runtime. It uses
the journal's authoritative paths, not mutable environment values. Missing or
corrupt target data does not prevent legacy recovery, but the journal records
the target as unavailable and blocks automatic re-upgrade until repaired.

## Uninstall and reinstall

Remove only resources owned by the recorded install:

```bash
./btctl uninstall --env /absolute/private/path/cwa-translate.env --yes
```

The retryable operation preserves CWA, external networks, the local image,
translation data, backups and final state evidence. Reinstalling the same
identity and data path after a completed uninstall is supported; a successful
reinstall moves the prior final record into `BT_STATE_DIR/history/`. Active,
partial or mismatched state is never overwritten.

## Failure behavior

- Invalid source identity stops before an operator receives the Docker socket.
- Invalid configuration, CWA evidence, names, paths or network preconditions
  stops before runtime creation.
- A failed startup removes only newly created translator containers and their
  private network; CWA, external networks, data, configuration and backups stay.
- State is committed only after live postconditions pass.
- Do not recover by exposing the API, setting disabled auth, editing generated
  state or applying generated Unraid templates manually.

For initial configuration and profile-specific prerequisites, see the
[managed install guide](../install/btctl.md). For symptoms and evidence
collection, see [troubleshooting](troubleshooting.md).
