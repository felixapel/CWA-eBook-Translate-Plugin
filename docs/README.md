# Documentation map

This index is the source of truth for durable project documentation. Current
work, release candidates and launch copy belong in Gitea issues or pull
requests, not in tracked Markdown.

## Install

- [Universal CWA and Kavita hub](install/universal-hub.md) — recommended
  one-container install, per-reader switches and split-topology migration.
- [Managed `btctl` installation](install/btctl.md) — Unraid and existing
  Compose split profiles, prerequisites and advanced isolation.
- [Kavita managed installation](install/kavita.md) — exact stock-Kavita EPUB
  boundary, isolated configuration, authentication and acceptance.
- [Community Applications](install/community-applications.md) — requirements
  and acceptance for the digest-pinned combined-image profile.
- [Authentik](install/authentik.md) — advanced forwarded-identity deployment.

## Operate

- [Lifecycle and recovery](operations/lifecycle.md) — doctor, adopt, upgrade,
  rollback, uninstall and failure behavior.
- [Troubleshooting](operations/troubleshooting.md) — symptom-led diagnosis and
  safe evidence collection.

## Reference

- [Architecture](reference/architecture.md) — runtime components, trust
  boundaries and data flow.
- [Compatibility](reference/compatibility.md) — certified and unsupported
  environments.
- [Configuration](reference/configuration.md) — managed inputs and low-level
  runtime settings.
- [Architecture decisions](decisions/README.md) — accepted and superseded ADRs.

## Maintain

- [Development](maintainers/development.md) — local setup and validation.
- [Release](maintainers/release.md) — Gitea-authoritative release and Community
  Applications promotion procedure.

## Canonical sources

| Fact | Source |
|---|---|
| Current code version | [`VERSION`](../VERSION) |
| Release history | [`CHANGELOG.md`](../CHANGELOG.md) |
| Managed install inputs | [`.env.hub.example`](../.env.hub.example), [`.env.example`](../.env.example), reader guide and `btctl` validation |
| Supported environments | `docs/reference/compatibility.md` |
| Architecture rationale | `docs/decisions/README.md` |
| Release state and review | Gitea pull request, issue or milestone |
| Published artifacts | Signed/annotated release tag, release record, GHCR digest and CA listing |
