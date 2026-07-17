# ADR-012: Publish a certified combined image for Community Applications

- Status: Accepted
- Date: 2026-07-16
- Partially supersedes: [ADR-008](ADR-008-source-only-releases.md)
- Amends: [ADR-002](ADR-002-split-non-root-runtime-roles.md)

## Context

The source-built `btctl` topology gives the strongest lifecycle, rollback, and
failure isolation, but it is too demanding as the only path for non-expert
Unraid users. Community Applications requires a maintained public image and a
template that DockerMan can install without host Python, Git, or a local build.

The image already contains a non-root combined `BT_ROLE=all` mode. Treating that
mode as unbounded legacy compatibility would be unsafe; certifying one narrow
profile is materially simpler than teaching Community Applications to manage
two coordinated containers.

## Decision

- Gitea remains the source and annotated-tag authority. GitHub remains the
  public mirror and publishes the matching GHCR artifact only after the natural
  Gitea tag workflow passes.
- A manual GitHub workflow accepts the exact existing tag and 40-character
  commit, re-runs release identity checks, rejects an existing immutable image
  tag, and uses only the repository `GITHUB_TOKEN`.
- The first image is `linux/amd64` only and is published only as immutable
  `2.2.1`; this release path does not create a moving `latest` alias. The final
  Community Applications template pins the exact manifest digest, with
  immutable `2.2.1` as a documented DockerMan compatibility fallback only if
  digest syntax is rejected.
- Publication requires pre-push split and combined smoke tests, a fail-closed
  high/critical Trivy scan, BuildKit SBOM and maximal provenance attestations,
  validation of the emitted OCI-index digest and attestation predicates, then
  an anonymous pull, another high/critical scan, and digest-pinned post-push
  smoke tests.
- The GHCR package must already be public before a release dispatch. This lets
  the workflow prove anonymously that the immutable version is unused before
  push and that the emitted digest is readable afterward. The operator records
  the emitted digest and separately verifies that the immutable version tag
  resolves to it.
- The certified Community Applications profile runs one `BT_ROLE=all`
  container as uid/gid `101:102`, with a read-only root filesystem, private
  writable `/app/data`, bounded `/tmp`, all capabilities dropped, and
  `no-new-privileges`. Only proxy port 8080 is mapped (host default 8385); API
  port 8390 is never published.
- Its first certified integration is Unraid 7.3.2 x86_64, CWA 4.0.6, native
  `cwa_session` authentication including strong session protection, and a local
  OpenAI-compatible LLM. CWA reverse-proxy-header login must be disabled for
  this native-session profile.
- `btctl` split roles remain the recommended advanced path for upgrades,
  rollback, adoption, Compose, and Authentik-forwarded identity.

## Consequences

- New Unraid users get a conventional Community Applications install without
  weakening authentication or exposing the API.
- The combined profile couples nginx and Gunicorn restarts and lacks `btctl`'s
  transactional lifecycle. That trade-off is accepted only for the certified
  CA scope and is stated in the install guide.
- GHCR availability becomes an operational dependency for the CA path; source
  releases and local builds remain available if the registry is unavailable.
- The public template repository stays quarantined until post-publication
  physical acceptance passes. Restoration is a deliberate replacement with
  the reviewed digest-pinned XML, never a checkout of the withdrawn draft.
- ARM64, arbitrary CWA versions, forwarded identity, custom reverse-proxy hop
  counts, and multi-container CA templates remain unsupported until separately
  tested.

## Verification

`scripts/ca-container-smoke.sh` proves rejection of an unsafe appdata bind, the
documented uid/gid and mode preparation, strong-session login, authenticated
batch translation through the proxy, persistent cache after container
recreation, absence of a published 8390 port, and the complete runtime sandbox.
Normal CI, the Gitea tag workflow, and the manual GHCR workflow all execute it.
Physical Unraid acceptance of the final digest and XML remains a mandatory
release gate.
