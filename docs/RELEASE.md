# Source release runbook

Gitea is the source and release authority. GitHub is the public source mirror
and GHCR publisher. Official artifacts are the one matching annotated tag, the
source archives Gitea generates for it, and—beginning with v2.2.1—the GHCR
linux/amd64 digest produced by the manual fail-closed publication workflow.

The authority decision is recorded in
[ADR-001](decisions/ADR-001-gitea-release-authority.md), the local source-build
policy in [ADR-008](decisions/ADR-008-source-only-releases.md), and the limited
image publication decision in
[ADR-012](decisions/ADR-012-community-applications-image.md).

## One-time remote prerequisites

- Protect Gitea `main` from direct and force pushes and require the backend,
  frontend, and Docker smoke contexts.
- Protect `v*` tags from updates/deletion and restrict creation to the
  maintainer.
- Keep Gitea Docker smoke on the trusted `weebdb-docker` label. The public
  GitHub mirror uses GitHub-hosted `ubuntu-latest` for Docker smoke; do not
  attach its public pull-request workflow to a homelab runner.

No Docker Hub, Cosign key, PAT, or release-specific Actions secret is required.
The manual GitHub workflow uses its built-in `GITHUB_TOKEN` for GHCR.

Gitea uses the first configured workflow directory that exists. Both
`.gitea/workflows/ci.yml` and `.gitea/workflows/release.yml` must remain
present. The Gitea and GitHub CI copies remain semantically identical except
for the provider-specific Docker runner binding, which contract tests enforce.

## What a release proves

Before candidate code runs, the validator from trusted `main` requires:

- a SemVer tag in the form `vMAJOR.MINOR.PATCH` with an optional prerelease;
- the same version in `VERSION`, `package.json`, both package-lock root fields,
  `BT_UI_VERSION`, both overlay cache-busters, and the first released Changelog
  entry;
- `HEAD`, the event SHA, and the annotated local tag to identify one commit;
- that commit to be reachable from freshly fetched Gitea `main`;
- GitHub to expose the same annotated tag object and peeled commit.

After preflight, the Gitea workflow runs the complete Python contracts and
dependency audit, frontend syntax/unit/real-Chromium gates, and hardened split
plus Community Applications Docker smoke tests. No Gitea workflow step receives
a publication credential. GHCR publication is a later, separate manual gate.

## Prepare a release

1. Choose a version that has never been used.
2. Update `VERSION`, `package.json`, both package-lock version fields,
   `BT_UI_VERSION`, both `?v=` values in `overlay/read.html`, and move the
   Changelog entries from `Unreleased` into a dated version section.
3. Commit the complete candidate so `HEAD`, `VERSION`, and every archived build
   input identify one clean immutable revision. Do not run the bootstrap smoke
   against uncommitted release edits.
4. Run the maintained local gate:

   ```bash
   .venv/bin/python -m py_compile \
     btctl.py btctl_container.py btctl_core.py btctl_compose.py \
     btctl_docker.py btctl_paths.py btctl_unraid.py btctl_auth.py btctl_lifecycle.py \
     auth.py server.py translator.py cache.py singleflight.py work_budget.py \
     proxy/render_config.py scripts/release_preflight.py
   bash -n btctl scripts/*.sh
   .venv/bin/python test_translation.py
   .venv/bin/python test_hardening.py
   .venv/bin/python -m unittest -v \
     test_btctl test_btctl_container test_btctl_compose test_btctl_unraid \
     test_btctl_auth \
     test_btctl_lifecycle test_work_budget test_provider_budget test_cache_v2 \
     test_context_cache test_singleflight test_auth test_ci_contract \
     test_install_docs test_release_contract test_supply_chain_contract \
     test_shell_contract \
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
   ./scripts/btctl-lifecycle-smoke.sh \
     "$CANDIDATE_IMAGE" "cwa-release-$CANDIDATE_SHA"
   ./scripts/btctl-bootstrap-smoke.sh "cwa-bootstrap-$CANDIDATE_SHA"
   ./scripts/ca-container-smoke.sh \
     "$CANDIDATE_IMAGE" "cwa-ca-$CANDIDATE_SHA"
   ```

5. Record the maintainer self-review, push the candidate through a protected
   Gitea pull request, and wait for every required check on its exact head.
6. Before merging, on physical stock Unraid 7.3.2 with no host Python or
   NerdTools, use the exact clean candidate commit to run the public `./btctl
   plan`, `install`, and
   `doctor` path. Then verify one real browser translation through the managed
   public route and record the commit plus result. Also validate the candidate
   `BT_ROLE=all` Community Applications configuration with port 8390 private,
   persistent cache recreation, and the final XML fields. This physical Unraid
   acceptance is mandatory before a v2.2.1 tag; simulated Docker gates are not
   substitutes.
7. Only after that evidence passes, merge through protected Gitea and wait for
   all required checks on the exact merged `main` commit. Fast-forward the
   GitHub mirror and verify both `main` refs resolve to that exact commit. If the
   merge changes the candidate commit ID, repeat physical acceptance on the
   merged commit before tagging; acceptance of a different SHA is not evidence
   for the release artifact.

For the v1-to-v2 cache transition, `test_cache_v2` is a release blocker. It
must prove that `translations` remains readable/writable by the v2.1.4 schema,
that v2 uses `translations_v2`, and that the unreleased draft layout is
normalized without losing either table.

## Create the source release

Stop here if physical Unraid and browser acceptance has not passed before merge
and on the exact merged commit, or if any protected Gitea check is missing,
skipped, or stale.
Do not create a tag to make a candidate appear complete.

Create one annotated tag object and push it to GitHub first because Gitea's
preflight verifies the public mirror. Push the same local object to Gitea
second:

```bash
VERSION=2.2.1
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

Then manually dispatch `.github/workflows/publish-image.yml` on GitHub with the
exact `v2.2.1` tag and its peeled 40-character SHA. The workflow must reject an
existing version tag, pass the pinned Trivy high/critical scan before
publication, publish SBOM and provenance attestations, validate the emitted
OCI-index digest, pull it anonymously, scan that exact digest, and repeat both
smoke profiles. Record its `GHCR_DIGEST` output. Do not retry a partially
successful publication or overwrite `2.2.1`; diagnose it and increment the
version.

Claude Code must repeat physical Unraid acceptance using that exact digest and
the final Community Applications XML. Only after that evidence passes may the
template repository be updated, CA Validate/Scan/submission run, and public
launch posts published.

Do not force-push, recreate, or delete a rejected tag. Correct the issue,
increment the version, and create a new tag.

## Install or roll back

An existing reference-Compose v2.1.4 deployment must use an offline migration
**before any v2.2.0 container starts**. The old release used one combined
`book-translator` container and the `./config/translator` bind mount; v2.2.0
uses split API/proxy roles and a separate data path. They are not interchangeable
without an explicit copy.

### Managed v2.1.4 upgrade

The supported operator path is the journaled lifecycle in `btctl`. Configure
the exact old container/bind mount and a distinct new data directory, then run:

```bash
./btctl plan --env /absolute/private/path/cwa-translate.env
./btctl upgrade --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

The command controls the only legacy writer, checkpoints and validates SQLite,
creates an external snapshot and separate target copy, installs the split
roles, and keeps the exact v2.1.4 runtime stopped and restartable. If public
browser acceptance fails after a completed cutover:

```bash
./btctl rollback --env /absolute/private/path/cwa-translate.env --yes
```

### Manual recovery reference for the retired Compose layout

The sequence below is retained as a disaster-recovery reference for the exact
repository-provided v2.1.4 Compose layout. It is not the primary v2.2 install
path and must not be mixed with a partially completed `btctl` journal.

Start from the v2.1.4 checkout. Stop the only writer, then create a new offline
snapshot outside the Git checkout so `cleanup_token` can never become an
untracked repository file:

```bash
git checkout v2.1.4
test "$(git describe --tags --exact-match)" = "v2.1.4"
docker stop book-translator

SOURCE_ROOT="$(pwd -P)"
OLD_DATA_DIR="$SOURCE_ROOT/config/translator"
OLD_IMAGE_ID="$(docker inspect book-translator --format '{{.Image}}')"
test -d "$OLD_DATA_DIR"
test -n "$OLD_IMAGE_ID"
BT_BACKUP_DIR="$HOME/cwa-backups/pre-v2.2.0-app-data"
export BT_BACKUP_DIR="$(realpath -m "$BT_BACKUP_DIR")"
case "$BT_BACKUP_DIR/" in "$SOURCE_ROOT/"*) echo "backup must be outside the checkout" >&2; exit 1;; esac
test ! -e "$BT_BACKUP_DIR"
install -d -m 0700 -- "$BT_BACKUP_DIR"
docker run --rm --user 0:0 --entrypoint /bin/sh \
  --mount "type=bind,src=$OLD_DATA_DIR,dst=/source,readonly" \
  --mount "type=bind,src=$BT_BACKUP_DIR,dst=/target" \
  "$OLD_IMAGE_ID" -ec 'cp -a /source/. /target/'
docker run --rm --user 0:0 --entrypoint python \
  --mount "type=bind,src=$BT_BACKUP_DIR,dst=/backup" \
  "$OLD_IMAGE_ID" -c 'import sqlite3; db=sqlite3.connect("/backup/translations.db"); db.execute("PRAGMA wal_checkpoint(TRUNCATE)"); assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"'
```

Leave the stopped `book-translator` container in place as the exact rollback
runtime; do not use `docker compose rm` or `--remove-orphans`. Check out the new
tag, build its image, create its still-stopped API container and copy the
offline snapshot into the new named volume:

```bash
git checkout v2.2.0
test "$(git describe --tags --exact-match)" = "v2.2.0"
export BT_PUBLIC_ORIGIN=http://192.168.1.10:8084  # replace with your origin
docker compose build book-translator-api
docker compose create --no-deps book-translator-api
API_CONTAINER="$(docker compose ps -aq book-translator-api)"
test -n "$API_CONTAINER"
DATA_VOLUME="$(docker inspect "$API_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"
test -n "$DATA_VOLUME"

docker run --rm --user 0:0 --entrypoint /bin/sh \
  --mount "type=bind,src=$BT_BACKUP_DIR,dst=/source,readonly" \
  --mount "type=volume,src=$DATA_VOLUME,dst=/target" \
  cwa-ebook-translate-plugin:local -ec '
    test -z "$(find /target -mindepth 1 -maxdepth 1 -print -quit)"
    cp -a /source/. /target/
    chown -R 101:102 /target
    chmod 0700 /target
    find /target -mindepth 1 -maxdepth 1 -type f -exec chmod 0600 {} +
  '
docker run --rm --user 101:102 --entrypoint python \
  --mount "type=volume,src=$DATA_VOLUME,dst=/app/data,readonly" \
  cwa-ebook-translate-plugin:local -c 'import sqlite3; db=sqlite3.connect("file:/app/data/translations.db?mode=ro", uri=True); assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"'

docker compose up -d --build
curl -fsS http://127.0.0.1:8084/bt-api/ping
```

Schema v2 writes `translations_v2` and leaves the v1 `translations` table
intact inside the copied database. Keep both the external snapshot and the
stopped old container through the rollback window.

To roll back, stop and remove only the new translator roles. Never add `-v` or
run `docker compose down -v`: the named v2 volume is retained for diagnosis or
re-upgrade. Restore the old Compose topology, then restart the preserved exact
v2.1.4 container, which still uses the untouched v1 bind mount:

```bash
export BT_PUBLIC_ORIGIN=http://192.168.1.10:8084  # same value used above
docker compose stop book-translator-proxy book-translator-api
docker compose rm -f book-translator-proxy book-translator-api
git checkout v2.1.4
test "$(git describe --tags --exact-match)" = "v2.1.4"
test "$(docker inspect book-translator --format '{{.State.Status}}')" = "exited"
docker compose up -d --no-build --pull never calibre-web
docker start book-translator
curl -fsS http://127.0.0.1:8390/ping
```

If the old container was deleted, stop: do not use the mutable `latest` image
in the v2.1.4 Compose file. Recreate it only from the exact v2.1.4 source and
the recorded environment. The external snapshot is the authoritative v1
recovery copy; the retained named volume is the authoritative v2 copy.

### Fresh v2.2.1 source-built install

These commands apply only after the official annotated tag exists and its
natural Gitea tag workflow has passed. Before tagging, physical acceptance uses
the exact clean merged commit as required under Prepare a release. With no
previous translator deployment, check out the official tag, copy `.env.example`
to a private external path, and use the managed lifecycle:

```bash
git checkout v2.2.1
test "$(git describe --tags --exact-match)" = "v2.2.1"
cp .env.example /absolute/private/path/cwa-translate.env
chmod 0600 /absolute/private/path/cwa-translate.env
# Edit the exact CWA, storage, origin, profile, and LLM values.
./btctl plan --env /absolute/private/path/cwa-translate.env
./btctl install --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

## Historical split tag

`v2.0.0` predates this contract and is intentionally divergent: the Gitea tag
peels to `57fe59a47e13af31dc98b33d409f6aadcf08a53e`, while the GitHub tag peels
to `a338ecc336ce519321dcf18ce58b1e5ec0c27034`. Do not rewrite either tag. The
parity rule applies to every new release.
