# ADR-010: Make `btctl` the fail-closed deployment authority

- Status: Accepted
- Date: 2026-07-14

## Context

The old Unraid helpers copied an overlay, built a mutable `latest` image, and
could not prove which containers, networks, or data they owned. A shallow
health check could also approve a runtime with authentication disabled. That
made upgrades and uninstall unsafe for non-expert operators.

The project ships source archives rather than registry images. An installation
therefore needs a reproducible identity derived from the exact checkout and a
durable record of what the installer may later change.

## Decision

`btctl` is the lifecycle authority for supported v2.2.0 installations.

- It accepts a deliberately small, non-executable `KEY=value` file.
- It requires a clean Git checkout, exact semantic `VERSION`, and full commit
  SHA. The local image name is `local/cwa-translate:<version>-<sha12>`; mutable
  tags such as `latest` are not part of the managed path.
- `plan` is deterministic, redacts credential-like values, and performs no
  deployment-file, state, CWA, or runtime-resource mutation. On stock Unraid,
  the containerized bootstrap accepted by
  [ADR-011](ADR-011-containerized-unraid-bootstrap.md) may build/remove
  temporary helper images and warm ordinary Docker build cache before the plan
  can run.
- State is schema-versioned JSON written atomically with mode `0600` in a
  private `0700` directory. New state and backup directory entries are fsynced
  through every newly created parent before runtime mutation, and the containing
  directory is fsynced after each evidence-file publish. State contains
  ownership and immutable identities, never API keys or browser credentials.
- An existing state directory is accepted only when it is owned by the current
  operator with exact mode `0700`; installer evidence files must be one-link,
  operator-owned regular files with mode `0600`. A fresh install refuses
  unknown entries instead of changing their ownership or permissions.
- Before the first Docker mutation, install writes and reads back a private,
  non-secret `install-attempt.json` bound to the install UUID, checkout
  identity, configuration fingerprint, and declared resources. Successful
  state commit removes it durably. When startup creates durable translation
  data but scoped runtime cleanup succeeds, the journal advances to `cleaned`;
  only the exact same plan may use that evidence to retry against the retained
  data. A failure before runtime restores prior `cleaned` evidence instead of
  minting new retry authority. Failed cleanup aggregates every scoped cleanup
  error and preserves a `cleanup-failed` journal instead of silently discarding
  failures.
- Resources are classified as `owned`, `adopted`, or `external`. A later
  lifecycle operation may mutate only an exact allowlist of `owned` resources
  whose live IDs and installation labels still match state.
- Translation data and migration snapshots are always preserved by ordinary
  uninstall, regardless of who created their directories.
- The API role never publishes a host port. Only the injection proxy may be
  published, and Authentik-forwarded mode publishes neither role.

## Compatibility policy

CWA `4.x` is the Tier 1 proxy-injection family. Exactly CWA `3.1.4` is retained
as a legacy source for the explicit v2.1.4 `upgrade` path; normal `install` and
`adopt` reject it. Other CWA `3.x`, prereleases, mutable version labels, and
unknown future majors fail closed until explicitly qualified.

## Consequences

Operators must keep the edited environment file and state directory outside
the checkout. This is intentional: updating or replacing source cannot erase
deployment ownership or secrets. Manual Docker changes can cause `doctor`,
upgrade, rollback, or uninstall to stop for drift instead of guessing.
