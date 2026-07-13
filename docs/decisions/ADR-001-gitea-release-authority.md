# ADR-001: Use Gitea as the sole release authority

## Status

Accepted; container publication details superseded by
[ADR-008](ADR-008-source-only-releases.md)

## Date

2026-07-12

## Context

The canonical repository is the private homelab Gitea instance. GitHub exists
as a public source mirror. At the time of this decision it also hosted the
project's container registry, so both services participated in a release
without having equal authority. Independent release workflows or tags can
create split-brain source or duplicate publications from different commits.

Gitea also selects the first configured workflow directory that exists. Once
`.gitea/workflows` is present, a GitHub-only release definition is neither an
authoritative nor a reliably exercised policy for the canonical repository.

## Decision

- Gitea is the only service allowed to initiate a release. The authoritative
  validation workflow is `.gitea/workflows/release.yml`; GitHub runs mirror CI
  only.
- Releases use annotated SemVer tags whose tag object and peeled commit already
  exist identically on GitHub. The candidate commit must be on Gitea `main`, and
  every version surface must match the tag.
- Untrusted tag code cannot execute before the validator from trusted `main`
  accepts the candidate.
- Backend, frontend, and container gates must all pass for the source tag. The
  validated annotated tag and Gitea-generated source archives are the release
  artifacts; see ADR-008.

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
- GitHub unavailability blocks a release rather than weakening the identity
  check.
- Gitea `main`, `v*` tags, and the trusted runner require one-time remote
  configuration outside this repository. Release-specific registry or signing
  secrets are not required.
- The historical divergent `v2.0.0` tags remain documented exceptions; new
  versions cannot use that precedent.
