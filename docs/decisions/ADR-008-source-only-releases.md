# ADR-008: Publish verified source releases without registry credentials

## Status

Accepted

## Date

2026-07-13

## Context

The project has one maintainer and is distributed as open source. Automated
multi-registry image publication, self-managed Cosign keys, SBOM/provenance
attestations, and privileged multi-architecture builders added substantial
operational work before any release could be created. Those controls protect
official container artifacts, but the project does not currently need to
publish official containers.

## Decision

- Official releases are annotated SemVer tags created in Gitea after the exact
  tag object and commit have been mirrored to GitHub.
- Gitea's source archives for the validated tag are the release artifacts.
- The tag workflow retains trusted-main preflight, version/parity checks,
  backend and dependency gates, real-browser tests, and the hardened container
  build/runtime smoke test.
- The project does not publish container images. Compose and Unraid users build
  the exact checked-out source locally.
- Release-specific registry credentials and Cosign keys are not configured or
  referenced by repository workflows.

## Alternatives Considered

### Keep image publication but make signing optional

Rejected because it preserves registry credentials, privileged publication,
and ambiguous artifact guarantees while removing only part of the complexity.

### Publish unsigned images

Rejected because an official mutable image without a maintained authenticity
policy is easier to misuse than an explicit source-only release.

### Keep the previous fail-closed image pipeline

Deferred. It remains appropriate if demand for official multi-architecture
images justifies the key custody, registry access, and maintenance burden.

## Consequences

- Contributors and users need only Git, Docker, and the checked-out release
  source; no project publication secret is needed.
- Installation performs a local Docker build and therefore takes longer than
  pulling a prebuilt image.
- Historical registry images remain historical artifacts and are not evidence
  of current releases.
- Reintroducing official images requires a new reviewed ADR and an explicit
  artifact authenticity and maintenance policy.
