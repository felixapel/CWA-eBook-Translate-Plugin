# ADR-007: Sign and verify release digests with a self-managed key

## Status

Accepted

## Date

2026-07-12

## Context

BuildKit can attach an SPDX SBOM and SLSA provenance to a multi-platform OCI
index, but unsigned metadata only describes an artifact; it does not establish
who authorized it. Tags are mutable and cannot be the signing identity. The
release authority is a private Gitea 1.26 instance. Its Actions implementation
does not provide GitHub's supported `id-token` permission, and a private issuer
is not automatically an identity trusted by the public Sigstore Fulcio service.

## Decision

- The release workflow signs each enabled registry repository at the exact
  multi-platform digest returned by the one Buildx publish step.
- BuildKit's default OCI attestation manifests are members of that
  multi-platform image index, so the Cosign signature over the exact index
  digest also binds the attached provenance and SPDX manifests that policy
  reads back. The workflow pins the BuildKit image digest to freeze that
  attestation representation.
- Buildx is installed from one versioned amd64 release asset only after its
  committed SHA-256 is verified. The BuildKit and binfmt/QEMU images are pinned
  by version and OCI index digest; only amd64/arm64 emulation is registered, and
  the builder container uses Docker bridge networking. Buildx 0.35 adds the
  daemon-side `network.host` authorization for container drivers; the workflow
  verifies that it is the only such authorization, forbids
  `security.insecure`, and never supplies the separate build-client `allow`
  needed to exercise it.
- Cosign 3.0.6 and its installer action are pinned. The installer verifies the
  downloaded binary against the reviewed release digest.
- An encrypted self-managed private key and password live only in Gitea Actions
  secrets. The matching public key is supplied separately and is the sole
  release verification root.
- Every signature contains the Gitea source SHA and release tag as mandatory
  annotations. The workflow immediately verifies them against the public key.
- Every published tag must resolve to the build digest. Both amd64 and arm64
  must expose a non-empty SPDX document. BuildKit fetches the verified public
  mirror by the full commit SHA so SLSA `configSource` is an immutable Git
  source rather than an unverified local-context VCS hint. Policy requires the
  exact source Git URL/SHA in the structured config-source and source-material
  fields, plus the normalized base-image URI and pinned digest in its material.
  Matching values in labels or unrelated metadata are rejected. Publication
  fails if any registry cannot store or return the signature or attestations.
- Deployment and rollback select an immutable digest and verify the signature
  before use; mutable aliases are discovery conveniences only.

## Alternatives Considered

### Sign mutable tags

Rejected because a tag can move after signing and therefore does not identify
the bytes that were reviewed.

### Public-Sigstore keyless signing from Gitea

Rejected for the current environment because its private Gitea job identity is
not a public Fulcio trust root. This can be reconsidered if the authoritative
runner gains a supported workload identity and verification policy.

### Trust BuildKit provenance without another signature

Rejected because attached metadata without an independent release key does not
authenticate the publisher.

## Consequences

- The release operator must provision, back up, and periodically rotate one
  encrypted Cosign key pair. Missing or mismatched key secrets block releases.
- A registry that lacks compatible OCI signature/attestation storage blocks
  publication instead of silently receiving a weaker artifact.
- The public source mirror must remain reachable after preflight because
  BuildKit fetches the already-verified commit directly; its SHA prevents a
  moved branch or tag from changing the build input.
- Updating Buildx, BuildKit, binfmt/QEMU, or their checksums/digests requires a
  release-policy self-review and the full protected gate; silent runner-side
  upgrades cannot alter a release build.
- Compromise of the signing key requires immediate key rotation, removal of
  mutable aliases to affected digests, and a new version; existing tags are not
  rewritten.
