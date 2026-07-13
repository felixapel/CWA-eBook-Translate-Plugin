# Source release runbook

Gitea is the release authority. GitHub is a public source mirror. Official
artifacts are the annotated tag and the source archives Gitea generates for
that tag; this project does not publish container images.

The authority decision is recorded in
[ADR-001](decisions/ADR-001-gitea-release-authority.md), and the source-only
policy in [ADR-008](decisions/ADR-008-source-only-releases.md).

## One-time remote prerequisites

- Protect Gitea `main` from direct and force pushes and require the backend,
  frontend, and Docker smoke contexts.
- Protect `v*` tags from updates/deletion and restrict creation to the
  maintainer.
- Keep the normal jobs on the trusted `ubuntu-latest` runner label and Docker
  smoke on the trusted `weebdb-docker` label.

No GHCR, Docker Hub, Cosign, package-registry, or release-specific Actions
secret is required.

Gitea uses the first configured workflow directory that exists. Both
`.gitea/workflows/ci.yml` and `.gitea/workflows/release.yml` must remain
present; the contract tests also keep the Gitea and GitHub CI copies identical.

## What a release proves

Before candidate code runs, the validator from trusted `main` requires:

- a SemVer tag in the form `vMAJOR.MINOR.PATCH` with an optional prerelease;
- the same version in `VERSION`, `package.json`, both package-lock root fields,
  `BT_UI_VERSION`, both overlay cache-busters, and the first released Changelog
  entry;
- `HEAD`, the event SHA, and the annotated local tag to identify one commit;
- that commit to be reachable from freshly fetched Gitea `main`;
- GitHub to expose the same annotated tag object and peeled commit.

After preflight, the workflow runs the complete Python contracts and dependency
audit, frontend syntax/unit/real-Chromium gates, and hardened split-role Docker
build/runtime smoke test. No workflow step receives a publication credential.

## Prepare a release

1. Choose a version that has never been used.
2. Update `VERSION`, `package.json`, both package-lock version fields,
   `BT_UI_VERSION`, both `?v=` values in `overlay/read.html`, and move the
   Changelog entries from `Unreleased` into a dated version section.
3. Run the maintained local gate:

   ```bash
   .venv/bin/python -m py_compile \
     auth.py server.py translator.py cache.py singleflight.py work_budget.py \
     proxy/render_config.py scripts/release_preflight.py
   .venv/bin/python test_translation.py
   .venv/bin/python test_hardening.py
   .venv/bin/python -m unittest -v \
     test_work_budget test_provider_budget test_cache_v2 \
     test_context_cache test_singleflight test_auth test_ci_contract \
     test_release_contract test_supply_chain_contract test_shell_contract \
     test_container_contract test_cleanup_token test_api_schema \
     test_error_privacy test_observability test_proxy_config test_live_scripts
   node -c static/translator.js
   node -c static/loader.js
   npm ci
   npm audit --audit-level=high
   npm test
   npx playwright install --with-deps --only-shell chromium
   npm run test:e2e
   PATH="$PWD/.venv/bin:$PATH" ./scripts/audit-deps.sh
   CANDIDATE_SHA="$(git rev-parse --short=12 HEAD)"
   CANDIDATE_IMAGE="cwa-translate-release-candidate:$CANDIDATE_SHA"
   docker build -t "$CANDIDATE_IMAGE" .
   ./scripts/container-smoke.sh "$CANDIDATE_IMAGE" "cwa-release-$CANDIDATE_SHA"
   ```

4. Record the maintainer self-review, merge through protected Gitea, and wait
   for all required checks on the exact merged `main` commit.
5. Fast-forward the GitHub mirror and verify both `main` refs resolve to that
   exact commit.

For the v1-to-v2 cache transition, `test_cache_v2` is a release blocker. It
must prove that `translations` remains readable/writable by the v2.1.4 schema,
that v2 uses `translations_v2`, and that the unreleased draft layout is
normalized without losing either table.

## Create the source release

Create one annotated tag object and push it to GitHub first because Gitea's
preflight verifies the public mirror. Push the same local object to Gitea
second:

```bash
VERSION=2.2.0
SHA=$(git rev-parse gitea/main^{commit})
test "$SHA" = "$(git rev-parse github/main^{commit})"
git tag -a "v$VERSION" "$SHA" -m "Release v$VERSION"
git push github "refs/tags/v$VERSION"
git ls-remote github "refs/tags/v$VERSION" "refs/tags/v$VERSION^{}"
git push gitea "refs/tags/v$VERSION"
```

Wait for the natural Gitea tag workflow. Do not manually rerun it. When all
four jobs pass, the Gitea tag and its generated `.zip`/`.tar.gz` source
archives are the official release.

Do not force-push, recreate, or delete a rejected tag. Correct the issue,
increment the version, and create a new tag.

## Install or roll back

Clone or check out the desired official tag and build it locally. Proxy mode
requires the exact origin used by the browser:

```bash
git checkout v2.2.0
export BT_PUBLIC_ORIGIN=http://192.168.1.10:8084  # replace with your origin
docker compose up -d --build
```

Before the first v2.2.0 start, stop every API writer and take an offline copy
of all `/app/data` files, including `translations.db`, any `-wal`/`-shm`
siblings, and `cleanup_token`. For the reference Compose deployment:

```bash
docker compose stop book-translator-proxy book-translator-api
install -d -m 0700 ./backups/pre-v2.2.0-app-data
API_CONTAINER="$(docker compose ps -aq book-translator-api)"
test -n "$API_CONTAINER"
docker cp "$API_CONTAINER:/app/data/." ./backups/pre-v2.2.0-app-data/
python3 -c 'import sqlite3; db=sqlite3.connect("file:backups/pre-v2.2.0-app-data/translations.db?mode=ro", uri=True); assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"'
```

Schema v2 writes `translations_v2` and leaves the v1 `translations` table
intact. An in-place code rollback therefore checks out `v2.1.4` and rebuilds
against the same volume; the old release ignores v2 rows. Keep the offline
copy until the new release has completed its rollback window. If integrity or
startup fails, preserve the v2 database separately and restore the complete
pre-upgrade copy before starting the old image.

## Historical split tag

`v2.0.0` predates this contract and is intentionally divergent: the Gitea tag
peels to `57fe59a47e13af31dc98b33d409f6aadcf08a53e`, while the GitHub tag peels
to `a338ecc336ce519321dcf18ce58b1e5ec0c27034`. Do not rewrite either tag. The
parity rule applies to every new release.
