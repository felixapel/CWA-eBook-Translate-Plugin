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
- Assign release jobs to a trusted, serialized runner with the `ubuntu-latest`
  label, a working Docker daemon, binfmt/QEMU support, and outbound HTTPS to
  GitHub, GHCR, Docker Hub (when enabled), and action sources.
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
container proxy/API/non-root smoke test run only after preflight. `publish`
depends on every gate, builds amd64 and arm64 once, and supplies that one build
to every enabled registry. Stable releases move the immutable full version,
`MAJOR.MINOR`, and `latest`; prereleases publish only their immutable full
version. The build also requests OCI provenance and an SBOM.

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
   adds dependency audits plus the proxy/API/non-root container smoke test:

   ```bash
   .venv/bin/python -m py_compile \
     server.py translator.py cache.py work_budget.py \
     scripts/release_preflight.py scripts/release_image_tags.py
   .venv/bin/python test_translation.py
   .venv/bin/python test_hardening.py
   .venv/bin/python -m unittest -v \
     test_work_budget test_provider_budget test_ci_contract \
     test_release_contract test_supply_chain_contract \
     test_cleanup_token test_api_schema \
     test_error_privacy
   node -c static/translator.js
   node -c static/loader.js
   npm ci
   npm audit --audit-level=high
   npm test
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
