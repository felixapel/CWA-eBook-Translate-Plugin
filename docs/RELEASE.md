# Release runbook

Gitea is the release authority for this project. GitHub is a public source and
container-registry mirror; GitHub Actions runs CI only and never publishes an
image. The authoritative workflow is `.gitea/workflows/release.yml`.
The rationale and rejected alternatives are recorded in
[ADR-001](decisions/ADR-001-gitea-release-authority.md).

## Remote prerequisites

Complete these once before the first production release:

- Protect Gitea `main` against direct/force pushes and require the exact
  backend, frontend, and Docker smoke status contexts used by this repository.
- Protect `v*` tags so only the release operator can create them and nobody can
  update or delete them.
- Keep normal preflight/backend/frontend jobs on the trusted `ubuntu-latest`
  label. Assign Docker smoke and publication jobs to the trusted, serialized
  `weebdb-docker` host label with a working Docker daemon, binfmt/QEMU support,
  and outbound HTTPS to GitHub, GHCR, Docker Hub (when enabled), Sigstore
  services, and action sources.
- Do not start another stable release until the current `publish` job has
  finished. Gitea 1.26 does not provide a release concurrency gate that this
  workflow can rely on; overlapping builds could move `latest` or `MAJOR.MINOR`
  in the wrong order.
- Create these Gitea repository Actions secrets:
  - `GHCR_USERNAME`: the GitHub account that owns
    `ghcr.io/felixapel/cwa-ebook-translate-plugin`.
  - `GHCR_TOKEN`: a GitHub personal access token **(classic)** with
    `write:packages`. Do not grant `repo` or `delete:packages`; the workflow
    only uploads and reads package metadata.
  - Optional `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. Define both or neither.
    Use a Docker Hub access token with repository write access, not an account
    password.
  - `COSIGN_PRIVATE_KEY`: an encrypted Cosign private key dedicated to this
    repository.
  - `COSIGN_PASSWORD`: the private-key encryption password.
  - `COSIGN_PUBLIC_KEY`: the matching PEM public key. It is not confidential,
    but keeping the workflow input beside the private-key configuration makes
    a mismatch fail closed.

Create the signing material once on an offline trusted workstation, using the
same reviewed Cosign major release as CI. Keep an encrypted offline backup,
copy the three values into Gitea Actions secrets, and securely remove the
working private-key copy:

```bash
umask 077
export COSIGN_PASSWORD='use-a-generated-password'
cosign generate-key-pair
# Store cosign.key, COSIGN_PASSWORD, and cosign.pub in the three Gitea secrets.
# Back up the encrypted key offline, then remove the workstation copy.
```

Distribute `cosign.pub` to deployment policy separately and record its SHA-256
fingerprint. A key rotation is a reviewed release-policy change: replace both
key secrets together, update deployment trust roots, and never re-sign or
rewrite an existing version tag.

Gitea stores repository secrets in the `secrets` context, but its built-in job
token cannot publish packages in this scenario, so the external registry PAT is
intentional. See the official [Gitea secret
contract](https://docs.gitea.com/1.26/usage/actions/secrets), [Gitea Actions
differences](https://docs.gitea.com/1.26/usage/actions/comparison), and [GHCR
authentication scopes](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Gitea uses the first workflow directory that exists. Both
`.gitea/workflows/ci.yml` and `.gitea/workflows/release.yml` must therefore stay
present; `test_release_contract.py` ensures the Gitea CI copy stays byte-equal
to `.github/workflows/ci.yml`. This follows Gitea's documented
[`WORKFLOW_DIRS`](https://docs.gitea.com/administration/config-cheat-sheet)
behavior.

## What the workflow proves

Before any candidate code or registry credential is used, preflight executes
the validator from trusted `main` and requires all of the following:

- a registry-safe SemVer tag: `vMAJOR.MINOR.PATCH` with an optional prerelease;
- the same version in `VERSION`, `package.json`, both package-lock root fields,
  `BT_UI_VERSION`, both overlay cache-busters, and the first released Changelog
  entry;
- `HEAD`, the event SHA, and the annotated local tag to identify one commit;
- that commit to be reachable from a freshly fetched Gitea `origin/main`;
- GitHub to expose the exact same annotated tag object and peeled commit.

Backend tests and dependency audit, frontend syntax/tests/audit, and the
split-role proxy/API sandbox smoke test run only after preflight. `publish`
depends on every gate. BuildKit then fetches the already-verified public mirror
by the full immutable commit SHA—not from the runner's mutable working tree—
builds amd64 and arm64 once, and supplies that one build to every enabled
registry. Stable releases move the immutable full version,
`MAJOR.MINOR`, and `latest`; prereleases publish only their immutable full
version. The build requests OCI provenance and an SPDX SBOM, signs the returned
digest in every enabled registry, verifies the release-tag/source-SHA
annotations, proves the exact configured image/tag sets resolve to that digest,
and validates both attestations for amd64 and arm64. Provenance must bind the
exact mirror Git URL and source SHA in BuildKit's structured `configSource` and
source material fields, and bind the normalized base-image identity to its
pinned digest in `resolvedDependencies`/`materials`; values in labels or
unrelated metadata do not satisfy policy. A missing secret, signature, tag,
platform, package inventory, or source identity fails the release.
These checks follow Docker's documented [immutable Git context](https://docs.docker.com/build/concepts/context/#git-repositories)
and [BuildKit SLSA field](https://docs.docker.com/build/metadata/attestations/slsa-definitions/)
contracts.

## Prepare a release

1. Choose a new version. Never reuse an existing version or tag.
2. Update all release surfaces:
   - `VERSION`
   - `package.json` `version`
   - `package-lock.json` top-level and `packages[""]` versions
   - `static/translator.js` `BT_UI_VERSION`
   - both `?v=` values in `overlay/read.html`
   - move the Changelog entries from `Unreleased` into a dated version section
3. Run the deterministic local gate. Protected CI independently repeats it and
   adds dependency audits plus the split-role non-root container smoke test:

   ```bash
   .venv/bin/python -m py_compile \
     auth.py server.py translator.py cache.py singleflight.py work_budget.py \
     proxy/render_config.py \
     scripts/release_preflight.py scripts/release_image_tags.py \
     scripts/verify_release_attestations.py
   .venv/bin/python test_translation.py
   .venv/bin/python test_hardening.py
   .venv/bin/python -m unittest -v \
     test_work_budget test_provider_budget test_cache_v2 \
     test_context_cache test_singleflight test_auth test_ci_contract \
     test_release_contract \
     test_release_attestations test_supply_chain_contract \
     test_shell_contract test_container_contract test_cleanup_token \
     test_api_schema test_error_privacy test_observability test_proxy_config
   node -c static/translator.js
   node -c static/loader.js
   npm ci
   npm audit --audit-level=high
   npm test
   npx playwright install --with-deps --only-shell chromium
   npm run test:e2e
   .venv/bin/python -m pip install \
     --require-hashes --only-binary=:all: -r requirements-audit.txt
   PATH="$PWD/.venv/bin:$PATH" ./scripts/audit-deps.sh
   docker build -t cwa-translate-release-candidate .
   ```

The base image digest, Alpine package versions, third-party Action commits, and
Python/npm locks are release inputs. Update them only in a reviewed change that
regenerates the relevant lock/contract and proves the container build. Exact APK
pins intentionally fail closed when an Alpine repository stops serving an
approved version; select and review the replacement instead of loosening pins.

4. Merge through Gitea and wait for all required `main` checks.
5. Mirror the exact `main` commit to GitHub and verify both remotes resolve to
   the same commit.

## Create and publish the tag

Create the tag once in a clean checkout containing both `gitea` and `github`
remotes. Push GitHub first because Gitea preflight queries the public mirror;
then push the same local tag object to Gitea:

```bash
VERSION=2.2.0
SHA=$(git rev-parse gitea/main^{commit})
test "$SHA" = "$(git rev-parse github/main^{commit})"
git tag -a "v$VERSION" "$SHA" -m "Release v$VERSION"
git push github "refs/tags/v$VERSION"
git ls-remote github "refs/tags/v$VERSION" "refs/tags/v$VERSION^{}"
git push gitea "refs/tags/v$VERSION"
```

After publication, record the immutable digest from the release job and verify
it again from a clean operator environment before deployment:

```bash
IMAGE=ghcr.io/felixapel/cwa-ebook-translate-plugin
DIGEST=sha256:<digest-from-the-authoritative-release-job>
TAG=v2.2.0
SHA=<40-hex-gitea-release-commit>
SOURCE_REPOSITORY=https://github.com/felixapel/CWA-eBook-Translate-Plugin.git
BASE_IMAGE=python:3.11-alpine

cosign verify --key cosign.pub \
  -a "release-tag=$TAG" -a "source-sha=$SHA" "$IMAGE@$DIGEST"
docker buildx imagetools inspect "$IMAGE@$DIGEST" \
  --format '{{json .SBOM}}' > sbom.json
docker buildx imagetools inspect "$IMAGE@$DIGEST" \
  --format '{{json .Provenance}}' > provenance.json
python3 scripts/verify_release_attestations.py \
  --sbom sbom.json --provenance provenance.json \
  --source-sha "$SHA" \
  --source-repository "$SOURCE_REPOSITORY" \
  --base-image "$BASE_IMAGE" \
  --base-digest <64-hex-digest-from-Dockerfile-FROM> \
  --platform linux/amd64 --platform linux/arm64
```

Deploy and roll back only with `$IMAGE@$DIGEST` after this verification. Do not
use `latest` as a rollback coordinate and do not rebuild an older version.

Do not force-push, recreate, or delete the tag if the workflow rejects it.
Correct the issue, increment the version, and create a new tag. If publication
fails after any registry accepted a manifest, stop: record the registry and
digest, do not rerun blindly, and recover under an explicit operator-approved
plan. The version tag is treated as immutable even after a partial failure.

## Historical split tag

`v2.0.0` predates this contract and is intentionally divergent: the Gitea tag
peels to `57fe59a47e13af31dc98b33d409f6aadcf08a53e`, while the GitHub tag peels
to `a338ecc336ce519321dcf18ce58b1e5ec0c27034`. Do not rewrite either historical
tag. The parity rule applies to all new releases after this workflow lands.
