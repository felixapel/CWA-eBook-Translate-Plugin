# ADR-002: Split API and proxy into non-root runtime roles

- Status: Accepted
- Date: 2026-07-12

## Context

The published image previously started as root, repaired bind-mount ownership,
then supervised Gunicorn as `appuser` and nginx as root. That coupled two
failure domains, required broad capabilities, and prevented a read-only root
filesystem in proxy mode.

Publishing separate API and proxy images would reduce installed packages per
role, but it would also create two independently versioned release artifacts
and duplicate the multi-architecture release path.

## Decision

One release image exposes explicit `BT_ROLE=api`, `BT_ROLE=proxy`, and
backwards-compatible `BT_ROLE=all` modes. Its declared user is the existing
stable `appuser` identity (`101:102`). No role performs ownership repair or
privilege changes at runtime.

The recommended Compose topology runs that image twice:

- the API role owns the SQLite volume and provider access;
- the proxy role owns browser-facing nginx and connects to CWA and the API;
- both use a read-only image filesystem, a bounded `/tmp` tmpfs, all Linux
  capabilities dropped, and `no-new-privileges`;
- only the proxy publishes a host port, and each role has its own health and
  restart lifecycle.

`BT_ROLE=auto` remains the image default. It selects the API role when
`CWA_UPSTREAM` is absent and the combined role when it is present, preserving
existing one-container deployments while Compose users migrate.

## Consequences

- A single digest still represents the complete release and architecture set.
- API and proxy failures are independently observable and recoverable.
- Bind-mounted `/app/data` directories must be writable by uid `101` before the
  long-running API starts. Managed Compose prepares the bind with a one-shot
  container from the built image, uses the invoking account's primary group as
  a private setgid read boundary for later lifecycle verification, and leaves
  the runtime at `101:102`. Unraid creates a `101:102` tree as root. The
  reference development Compose file continues to use a named volume.
- Managed Compose cache files use `0640` and the bind uses `2750`; Unraid and
  unmanaged defaults remain `0600` and `0700`. This avoids requiring root for
  rollback or reinstall while granting no access to host users outside the
  selected operator group.
- The combined role remains supported for compatibility but is not the
  production recommendation.
- nginx and the standard-library-only validated config renderer remain present
  in the shared image even for the API role; this is the accepted cost of
  keeping one auditable release artifact. gettext/envsubst was removed.

## Verification

`scripts/container-smoke.sh` builds both roles from one image, enforces the
runtime sandbox, checks routing, stops the API cleanly, and proves nginx remains
alive and fails closed while its backend is unavailable.
