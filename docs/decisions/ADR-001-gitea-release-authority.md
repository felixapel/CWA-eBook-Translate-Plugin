# ADR-001: Use Gitea as the sole release authority

## Status

Accepted

## Date

2026-07-12

## Context

The canonical repository is the private homelab Gitea instance. GitHub exists
as a public source mirror and hosts GHCR, so both services participate in a
release without having equal authority. Independent release workflows or tags
can create split-brain source, duplicate publications, and mutable image aliases
that point at different commits.

Gitea also selects the first configured workflow directory that exists. Once
`.gitea/workflows` is present, a GitHub-only release definition is neither an
authoritative nor a reliably exercised policy for the canonical repository.

## Decision

- Gitea is the only service allowed to initiate a release. The sole publishing
  workflow is `.gitea/workflows/release.yml`; GitHub runs mirror CI only.
- Releases use annotated SemVer tags whose tag object and peeled commit already
  exist identically on GitHub. The candidate commit must be on Gitea `main`, and
  every version surface must match the tag.
- Untrusted tag code cannot execute before the validator from trusted `main`
  accepts the candidate.
- Backend, frontend, and container gates must all pass before registry secrets
  enter scope. One multi-platform build publishes all requested registry tags
  with provenance and an SBOM.
- Prereleases publish only their immutable full-version tag. Stable releases
  may additionally move the minor and `latest` aliases, with releases serialized
  operationally because Gitea 1.26 has no dependable concurrency primitive for
  this workflow.

## Alternatives Considered

### Independent Gitea and GitHub releases

Rejected because two authorities can publish the same version from different
commits or race mutable aliases. Matching branch names do not prove matching
tag objects.

### GitHub as the release authority

Rejected because the private Gitea repository is canonical and owns branch/tag
policy. Moving authority to the public mirror would make its availability and
configuration the source of truth.

### Mirror the GitHub tag after Gitea publishes

Rejected because publication would begin before the public source tag was
provably identical. A failed mirror after pushing an image would leave public
artifacts without a matching public source tag.

## Consequences

- Operators must push the one local annotated tag object to GitHub first and to
  Gitea second, following `docs/RELEASE.md`.
- GitHub or registry unavailability blocks a release rather than weakening the
  identity check. A partial publication is never blindly retried under the same
  version.
- Gitea `main`, `v*` tags, secrets, and the trusted runner require one-time
  remote configuration outside this repository.
- The historical divergent `v2.0.0` tags remain documented exceptions; new
  versions cannot use that precedent.
