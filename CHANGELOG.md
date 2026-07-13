# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Authentication now fails closed before cache/provider work. The recommended
  proxy topology validates the existing HttpOnly CWA session with bounded,
  coalesced probes; trusted forwarded identity and shared-token compatibility
  are explicit alternatives, while anonymous mode is development-only.
- Browser loaders no longer recover API credentials from `localStorage`.
  Credentialed CORS accepts exact origins only, identity headers are honored
  only from allowlisted proxy CIDRs, and rejected auth attempts have a separate
  rate limit.
- CWA-session probes now require the exact authenticated endpoint and a bounded
  JSON task-list response, including an absolute streaming deadline. The Unraid
  overlay helper is CWA-session-only, and token/forwarded browser requests omit
  CWA cookies. Its legacy direct-port topology also rejects HTTPS reader origins
  because its API route is HTTP-only.
- Remote/cloud fallback is now fail-closed per request. The reader starts each
  book tab opted out, shows an explicit data-export warning, never persists the
  choice, and includes consent in cache and singleflight policy boundaries.
- Both request limiters cap active client buckets and reject unseen identities
  under saturated cardinality instead of allowing source-IP churn to grow
  process memory without bound.
- Translation endpoints now reject non-object JSON and invalid Unicode before
  provider dispatch. Batched provider responses use unpredictable segment IDs
  and a strict, bounded response envelope so prompt output cannot be mistaken
  for another paragraph.
- Every request has atomic limits for provider attempts, input bytes, output
  tokens, and wall time; a process-wide upstream gate prevents concurrent
  requests from bypassing those budgets. Streaming provider responses are cut
  off before oversized JSON is materialized.
- Cleanup credentials are created atomically across workers with mode `0600`;
  persistence failures disable the privileged endpoint instead of falling back
  to a logged or process-local secret.
- Provider failures, framework errors, and deep health results return stable
  sanitized envelopes. The provider-touching health probe requires an API token;
  liveness and readiness remain shallow and never spend provider capacity.
- Python dependencies, audit/compiler tooling, npm artifacts, the Python base
  image, direct/transitive Alpine packages, Node.js, and third-party Actions are
  pinned to reviewed versions, hashes, digests, or commits.
- The published image now declares its stable non-root user; API and nginx run
  as independent roles with read-only root filesystems, zero capabilities, and
  no root ownership-repair supervisor in the recommended Compose topology.
- Cache schema v2 never reuses unscoped v1 rows, stores no source paragraph,
  hashes tenant/book/chapter identifiers, and enforces private files plus
  mandatory TTL/cap. Browser persistence is opt-in and legacy unscoped browser
  entries are purged on upgrade.
- Proxy startup now validates exact upstream URLs and a required public origin.
  nginx forwards only that fixed host/scheme, replaces spoofable forwarding
  chains with its observed peer, emits relative self-generated redirects, and
  enforces a finite configurable CWA upload cap. It also strips CWA's configured
  reverse-proxy login header instead of accepting a browser-supplied identity.

### Added

- A production-readiness record maps every 2026-07-12 audit finding to its
  repository control, reproducible acceptance gate, historical exception, or
  operator-owned Gitea/release prerequisite.
- A required real-Chromium E2E gate verifies reader-route loader isolation,
  DOM rendering, API payloads, explicit cloud consent, console/network health,
  screenshots, and the accessibility tree in both CI and release workflows.
- Request work-budget and global upstream-cap contracts with concurrency,
  cancellation, deadline, fallback, and response-size regression tests.
- Gitea-authoritative release preflight, exact annotated GitHub mirror-tag
  verification, multi-registry image publishing, OCI provenance, SBOM output,
  and a fail-closed release runbook.
- Digest-bound Cosign signatures plus immediate tag, source-SHA, base-image,
  SPDX inventory, provenance, and multi-platform policy verification.
- Reproducible Python lock generation on Python 3.11 plus committed runtime,
  auditor, and compiler hash locks.
- A shared container smoke harness that proves role isolation, proxy routing,
  immutable root filesystems, exact runtime identity, and clean shutdown.
- Atomic group-cache and prompt-fingerprint contracts covering provider/model,
  tenant, book, chapter, context, language, protocol, migration, and retention.
- Bounded singleflight coalescing for identical active translation operations,
  with tenant/context isolation and pressure counters in `/metrics`.
- Fixed-cardinality HTTP, authentication, rate-limit, work-budget, provider,
  and partial-batch failure counters without content-derived metric labels.

### Changed

- Reader controls now expose a named toolbar, live status, honest progress,
  keyboard-operable settings actions, synchronized popover/switch state, and
  a visible non-fatal wait message while the API is rate limiting requests.
- CI and release gates now run every backend contract suite, the complete locked
  npm tree audit, and a required proxy/API/non-root container smoke test. Gitea
  and GitHub CI definitions remain byte-identical by contract.
- The live rate-limit probe is import-safe, authenticated, timeout-bounded, and
  fails closed while using same-language requests that never call a provider.
  Output-token scaling also rejects non-finite or non-positive startup values.
- `/ping`, `/health`, and `/ready` are cheap local probes; `/health/deep` is the
  explicit authenticated provider diagnostic.
- Deployment helpers use strict shell mode, safe remote argument serialization,
  fail-closed health/hash checks, and the same non-root sandbox as CI.
- The proxy renderer is a standard-library validator with atomic private output;
  gettext/envsubst and their Alpine dependency surface were removed.
- The split Compose proxy reaches the API through a network-scoped alias on the
  fixed trusted-proxy subnet, avoiding ambiguous multi-network DNS routing.
- The browser retries only bounded `429` admission rejections. Ambiguous
  timeouts, network failures, and invalid responses require an explicit user
  retry so they cannot duplicate provider work still running server-side.

## [2.1.4] - 2026-07-08

Security follow-up to the deep re-audit (rate-limit hardening for proxy/
reverse-proxy deployments).

### Security
- **Rate-limit bypass via forged `X-Forwarded-For`**: the limiter keyed on the
  FIRST XFF hop, which is client-controlled (standard proxies *append* the
  address they saw — they don't overwrite). In the trusted-proxy
  configurations meant to be production-safe, any client could rotate forged
  first hops and get unlimited fresh rate-limit buckets. The key is now the
  LAST hop — the one entry appended by the trusted proxy that a client cannot
  forge.
- **Proxy mode now defaults `BT_TRUSTED_PROXIES=127.0.0.1/32`**: all API
  traffic arrives via the in-container nginx, so the API previously saw every
  reader as `127.0.0.1` — one shared rate-limit bucket for the whole
  household, where a single aggressive client starved everyone. The
  in-container proxy is trustworthy by construction; per-client limiting now
  works out of the box (override by setting `BT_TRUSTED_PROXIES` explicitly).

### Fixed
- CORS private-LAN matcher now also accepts `127.x.x.x` (beyond `127.0.0.1`)
  and IPv6 `[::1]` origins.

## [2.1.3] - 2026-07-07

Deep-audit follow-up: content fidelity and retry robustness.

### Fixed
- **"Translated" mode permanently stripped the paragraph's markup** (italics,
  bold, links) when toggling back to Original/Bilingual, because restoration
  used plain text. The original inner HTML is now preserved (WeakMap) and
  restored intact; the plain text in `dataset.originalText` stays the hash
  source so cache keys are unchanged.
- **Failed translation batches were silently dropped** while the status bar
  claimed "Retrying…". Transport errors, timeouts and backend error markers
  now re-queue the affected paragraphs for a bounded retry (3 attempts per
  item; drops count toward the honest chapter counter, retries stay pending).
- The client's 90s safety-net timeout was indistinguishable from a deliberate
  abort (mode/language/page change): a genuinely hung request was treated as
  stale work and lost. Timeouts now retry; deliberate aborts just discard.
- **Connection errors never retried**: a single timeout/refused connection
  burned the provider on the first attempt. Transient no-response failures now
  retry once with a short pause before deferring to the fallback provider.
- CJK source text (Chinese/Japanese/Korean) was under-budgeted ~3x by the
  proportional output-token cap (flat 3.5 chars/token estimate) and could
  truncate those translations; token estimation is now script-aware
  (~1.5 chars/token for CJK ranges).
- CORS preflight (`OPTIONS`) requests no longer consume rate-limit budget —
  previously every cross-origin request cost 2x, and a 429 on a preflight
  surfaced as a cryptic CORS error instead of a rate limit the frontend can
  honor.
- A malformed backend response with more translations than requested could
  crash the frontend pump (defensive length guard added).
- `deploy_unraid.sh` recreated the API container without the
  `net.unraid.docker.managed=dockerman` label or autostart entry — the exact
  omission that once caused the container to vanish as an "orphan" (see
  `docs/DEPLOY_UNRAID.md`). The script now matches the doc.

### Changed
- **Alt+T** now also works while the reader iframe has focus (the shortcut is
  attached inside each chapter document).

### Tests
- New checks: CJK token budgeting, OPTIONS rate-limit exemption, and frontend
  regression guards for markup preservation, bounded batch retry, and
  timeout-vs-abort distinction.

## [2.1.2] - 2026-07-02

Reader-formatting and language-picker fixes from real-world reading reports.

### Fixed
- **Whole chapter translated as one giant block**: hierarchy de-duplication
  kept the ANCESTOR when both a chapter wrapper (e.g.
  `<section class="chapter">`, matched via `[class*="chapter"]`) and its
  paragraphs were selected. Dedup now keeps the smallest units and drops
  ancestors; `section`/`article`/`aside` wrappers with block children are
  excluded outright. Regression-tested with the wrapper shape
  Calibre-converted epubs actually ship.
- Oversized elements (>7500 chars — always mis-detected containers) are
  skipped client-side so they can never 413 an entire batch.
- **Language dropdown rendered blank rows until hovered/scrolled** (Chromium
  paint glitch): options inherited the pill's translucent background —
  explicit opaque colors on `option`/`optgroup` fix it.
- Language list is now visibly alphabetical: A-Z group labels lead with the
  English name ("Albanian — Shqip"), so sorting is obvious and native
  type-to-jump works on latin keyboards; top-10 group keeps endonym-first
  ("Español — Spanish").

## [2.1.1] - 2026-07-02

Release-hygiene and UX-truth patch (external deep-audit follow-up).

### Fixed
- **Proxy loader cache-busting**: `loader.js` hardcoded `?v=2.0.0` for the
  CSS/JS assets it loads, so browsers could keep stale UI files across
  releases. The loader now inherits the version from its own injected `?v=`
  query param — version-free by construction.
- **Honest chapter progress in the control bar**: the counter only measured
  the background-prefetch queue, showing "Chapter 0/91" while the visible
  page was actively translating. Progress is now derived live from
  paragraphs processed + in flight + queued (visible AND prefetch), shown in
  both the page and chapter states; rate-limited batches are re-queued
  without being counted.
- `/cache/cleanup` auto-generated token is **no longer logged** — logs show
  only the persisted path (`docker exec <c> cat /app/data/cleanup_token`).
- Batch `source_lang == target_lang` short-circuit now returns the full
  response contract (`backends[]`/`cached[]`).
- SQLite schema default `'MiniMax-M3'` replaced with `'unknown'` (matches the
  release-notes claim; writes always pass an explicit model).
- Release notes renamed `RELEASE_NOTES_2.0.0.md` -> `RELEASE_NOTES_2.1.0.md`
  (the content describes the 2.1.0 hardening sprint).

### Added
- `BT_MAX_UPSTREAM_INFLIGHT` — process-wide cap on in-flight LLM calls
  (default 0 = unlimited). Bounds total GPU/API pressure independently of the
  per-request `BT_MAX_CONCURRENT` (worst case was 8 threads x 2 = 16 calls).
- `BT_HEALTH_DETAILS=false` hides backend names/latency from unauthenticated
  `/health` when a token is configured (`/ping` stays the bare liveness probe).
- nginx: 3 MB body cap on `/bt-api/` (CWA uploads keep unlimited).

### Changed
- Token comparisons use `hmac.compare_digest` (constant-time).
- Entrypoint ownership repair is now conditional and non-recursive (only when
  appuser lacks write access; no `-R` over host appdata).
- `docker-compose.yml` no longer publishes the direct API port 8390 by
  default in proxy mode (same-origin `/bt-api` is the supported path).
- CI `npm audit` covers dev dependencies (jsdom is the only dependency and
  `--omit=dev` audited nothing).

## [2.1.0] - 2026-07-02

Production-hardening release: security audit fixes (Hermes sprint) plus
review fixes on top.

### Fixed
- **Container crashed with a root-owned bind-mounted data dir** (every real
  deployment): the non-root gunicorn could not open the SQLite DB. The
  entrypoint now chowns `/app/data` before dropping privileges.
- **Fallback cache poisoning residue**: translations produced by the fallback
  provider were cached under the primary model's key. Writes now use the
  model that actually served the text; lookups probe primary then fallback
  keys, so outage-era work is never re-paid.
- Cleanup-token auto-generation now persists the token when the token file
  exists but is empty.
- `/stats` reported `db_size_mb: 0.0` under WAL (now sums `-wal`/`-shm`).
- `source_lang == target_lang` no longer spends an LLM call (echo passthrough).
- Cache keys are scoped by model, so switching `LLM_MODEL`/provider never
  serves stale translations from the previous backend. Existing cache entries
  (model-less keys) cold-miss once and re-warm naturally.

### Added
- Non-root API: gunicorn runs as `appuser` via gosu (nginx keeps root for the
  listen port); `LLM_API_KEY` is no longer baked as an image ENV.
- `BT_TRUSTED_PROXIES` CIDR allowlist for X-Forwarded-For (spoof-safe
  replacement for the dev-only `BT_TRUST_PROXY=true`).
- `BT_MAX_CONTENT_LENGTH` (default 2 MB) WSGI-level request cap with a JSON
  413 handler; sized for 50-paragraph CJK batches.
- `/cache/cleanup` always requires auth; when `BT_API_TOKEN` is unset a token
  is auto-generated, persisted (0600) and logged once.
- `/translate/batch` returns per-paragraph `backends[]` and `cached[]`
  attribution; `/stats` exempt from rate limiting (still behind auth).
- CI: pip-audit + npm audit dependency gates, Docker build smoke test,
  `scripts/audit-deps.sh`; `test_hardening.py` suite.
- Private LAN IPs replaced with placeholders across public docs/scripts.

## [2.0.0] - 2026-07-02

### Added
- **100+ target languages** (was 34), mirroring Gemma 4's pre-training
  coverage. The picker shows the 10 most-spoken languages first, then all
  other languages A–Z with native names (endonyms); native select type-to-jump
  works as search. Browser-language default mapping expanded to ~45 locales.
  Frontend and backend language sets are test-enforced to stay identical.
  README documents Gemma 4 as the default/base model and the honest quality
  tiers (35 first-class languages vs the wider pre-trained set).
- **Proxy-injection mode** — the recommended install. Set `CWA_UPSTREAM` and the
  container proxies CWA on `BT_PROXY_PORT` (default `8080`), injecting a single
  `<script>` tag into reader pages. Stock CWA image, no template mounts, no CORS
  (same-origin `/bt-api`), survives CWA updates. New files: `static/loader.js`,
  `proxy/nginx.conf.template`, `docker-entrypoint.sh`.
- Request-size caps: `BT_MAX_BATCH_PARAGRAPHS` (default 50) and
  `BT_MAX_PARAGRAPH_CHARS` (default 8000); oversized requests are rejected with
  `413` instead of triggering unbounded LLM work.
- Configurable CORS: `BT_ALLOWED_ORIGINS` (comma-separated exact origins) and
  `BT_ALLOW_PRIVATE_LAN` (default `true`: localhost/RFC1918 origins allowed).
- `BT_TRUST_PROXY` — opt-in rate limiting by the first `X-Forwarded-For` hop
  behind a trusted reverse proxy.
- `BT_CACHE_MAX_ENTRIES` — optional cap on the SQLite cache with oldest-first
  eviction.
- `VERSION` file as the single version source; version reported in `/health`.
- GHCR release workflow (`.github/workflows/release.yml`): multi-arch
  (amd64/arm64) image published on `v*` tags (GitHub only).
- 20 new self-contained test assertions (context prompt format, caps, cleanup
  validation, cache normalization, hit counting, provider attribution, CORS).

### Fixed
- **Context-aware translation** (`BT_CONTEXT_WINDOW`) was broken: the batch
  prompt embedded Python list reprs (`['para one', ...]`) between segment
  markers. Context is now one plain-text `[CONTEXT]` block placed before the
  first `@@SEG@@` marker.
- Cache keys are now whitespace-normalized, so the single and batch endpoints
  share entries — the same paragraph is never translated (and paid for) twice.
- `hit_count` / `/stats` `total_hits` now reflect real cache hits (previously
  the count was reset to 1 on every write and never incremented).
- `/cache/cleanup` validates `days` (integer 1–3650); a negative value
  previously deleted the entire cache.
- Batch results now report the provider that actually served each paragraph
  (the fallback when the primary failed), not the configured primary.
- `Retry-After` and `X-Request-ID` are exposed to cross-origin JS via
  `Access-Control-Expose-Headers`.
- Proxy mode passes through the upstream `X-Forwarded-Proto` (map with
  `$scheme` fallback), so HTTPS sessions behind SWAG/Traefik/NPM keep secure
  cookies working. Verified against a real SWAG + Cloudflare deployment.

### Changed
- `docker-compose.yml` ships proxy-injection mode by default and pins CWA to
  `v4.0.6` (tested version) instead of `latest`.
- The container entrypoint supervises gunicorn (+ nginx in proxy mode) and
  exits if either dies, so the restart policy recovers a half-dead container.
- Versioning unified to SemVer `2.0.0` across `VERSION`, `package.json`, the
  UI (`BT_UI_VERSION`), and cache-bust query strings.

### Removed
- **Breaking:** legacy API-key loading from `auth.json`, `.env` parsing, and
  the `MINIMAX_API_KEY` fallback. Set `LLM_API_KEY` (and
  `LLM_FALLBACK_API_KEY`) instead.
- **Breaking:** hardcoded CORS whitelist entries (a personal domain and the
  `192.168.0.x` subnet). Use `BT_ALLOWED_ORIGINS` / `BT_ALLOW_PRIVATE_LAN`.

### Documentation
- README restructured around the proxy-injection install; bind-mount documented
  as the advanced/development path with its drift caveat.
- `docs/DEPLOY_UNRAID.md`: documented running the API as a proper
  **Unraid-managed** container (the `net.unraid.docker.managed=dockerman` label +
  `/var/lib/docker/unraid-autostart` entry + the Docker template), so it's treated
  as a first-class container and starts with the array — instead of a bare
  `docker run` that Unraid can treat as an orphan and remove. Fixed the
  "Update the Backend API" steps: `docker restart` does **not** load a rebuilt
  image, so the container must be recreated (the `/app/data` bind mount keeps the
  SQLite cache). Reframed the doc's intro as a worked example (substitute your own
  host/paths) rather than one specific machine.
- `my-book-translator-api.xml` (Unraid template) now exposes the full env surface
  (`BT_CONTEXT_WINDOW`, `BT_MAX_TOKENS`, `BT_BATCH_MAX_TOKENS`) so every tunable is
  editable from the Unraid UI.

## [1.4.0] - 2026-07-01
UI version marker: `2026-07-01-compact-bar-v1`.

### Changed
- **More compact control bar + tighter status copy.** With the flicker fixed
  (1.3.3), the status zone's stability no longer needed a wide fixed reservation:
  shrank `#bt-status` from a fixed `230px` to `140px` (the status strings are now
  short enough that this fits every locale without truncating, verified by
  measuring against the actual font). The bar is ~90px narrower while translating.
- Shortened and tightened the status strings across all locales: e.g.
  "Translating current page…" → "Translating…", "Preparing next paragraphs… N/M"
  → "Chapter N/M" (which is also more precise — it shows exact progress), "⚠ Error
  — click to retry" → "⚠ Retry", "Rate limited — waiting Ns…" → "Waiting Ns…".
  Status text is slightly lighter (weight 500) for a cleaner look.

## [1.3.3] - 2026-06-30
UI version marker: `2026-06-30-bar-flicker-fix-v1`.

### Fixed
- **Control bar rapidly blinking/flickering while a translation is actively in
  progress.** Root cause: the position-based page-turn detector polls every
  350ms for "did the first visible paragraph change" as a proxy for page
  navigation. Inserting a bilingual translation block under a paragraph
  increases that paragraph's rendered height, which reflows the layout and can
  shift which paragraph counts as "first visible" — with no real page turn
  involved. That false positive forced a full `newGeneration()` reset (hides
  the status pill) immediately followed by a fresh `translateCurrentPage()`
  (shows it again) — and because the plugin's own rendering kept shifting the
  layout throughout an active translation pass, this repeated on essentially
  every poll tick for as long as work was in progress, i.e. exactly the
  "blinking fast while a job is running" behavior reported (two rounds of
  animation/width tuning in 1.3.1/1.3.2 did not touch this — it was a genuine
  logic bug, not a CSS/timing issue). Fixed by gating the position-based poll
  on being genuinely idle (`!isTranslating && !isPrefetching`); real
  navigation while work is in flight is still caught immediately via the
  epub.js `relocated`/`rendered` hooks, which don't depend on visual position
  at all. Added a second line of defense: even while idle, a new position must
  be observed on two consecutive polls (~700ms apart) before being accepted,
  guarding against any other transient layout blip.
- Added a regression guard in `test_frontend.js` that locks the fix's source
  pattern in place (a full behavioral reproduction needs a real layout engine
  — jsdom does not compute live reflow from DOM changes).

## [1.3.2] - 2026-06-30
UI version marker: `2026-06-30-status-bar-width-fix-v1`.

### Fixed
- **Control bar visibly expanding/contracting in width.** `#bt-status` (the
  spinner+text zone) was sized with `max-width: 230px` — a *cap*, not a fixed
  size — so it shrink-wrapped to whatever status string was currently showing.
  Cycling between "Translating current page…", "Preparing next paragraphs… N/M",
  and "✓ Ready" (different lengths) made the whole pill resize every time the
  text changed, on top of the 1.3.1 progress-fill fix (a separate issue inside
  the same bar). Changed to a fixed `width: 230px` — the box now reserves
  constant space regardless of which status is showing; `#bt-status-text` still
  ellipsis-truncates anything that doesn't fit, identical to before.

## [1.3.1] - 2026-06-30
UI version marker: `2026-06-30-progress-bar-fix-v1`.

### Fixed
- **Jittery "Translating current page…" progress indicator.** The indeterminate
  state animated a fixed-width (35%) fill block sliding edge-to-edge inside the
  pill via `margin-left`, clipped by the pill's rounded `overflow: hidden` corners.
  Because the visible portion of that block shrank/grew as it slid past the
  corners, and the 1.1s loop snapped hard back to the start each cycle, it read as
  fast, distracting "wide/narrow" jitter — exactly what was reported. Replaced with
  a calm full-width opacity breathe (1.8s, no position or width change at all), so
  there's nothing to clip or snap. Applies to every state that reuses the
  indeterminate look (translating, retrying after a transient error).

### Changed (production-readiness audit — security, env vars, install, CI, docs)
- **Security:** scrubbed a committed Gitea PAT from `package.json`'s
  `repository.url` (was a live credential in git history); `.gitignore` now
  excludes `.env` / `auth.json` / `*.b64`.
- **Env vars made to actually work:** `PORT` was declared (Dockerfile `ENV`, docs)
  but never read anywhere — gunicorn and the dev `app.run()` fallback both
  hardcoded `8390`. Wired it through both (verified end-to-end on an isolated test
  container: remapped port, healthcheck followed it, graceful shutdown ~1s).
  Removed `prefetchPages` — declared in `read.html`'s `window.BOOK_TRANSLATOR` but
  never read by `translator.js`. Documented 7 previously-undocumented-but-working
  vars: `BT_MAX_TOKENS`, `BT_BATCH_MAX_TOKENS`, `BT_RATE_LIMIT_PER_MINUTE`,
  `BT_RATE_LIMIT_RETRY_AFTER`, `DB_PATH`, `PORT`, `MINIMAX_API_KEY`.
- **Install path fixed:** README + `install_unraid.sh` pointed at a nonexistent
  `raw.githubusercontent.com/username/...` URL (`curl | bash` to a 404).
  Re-pointed installation around "clone the repo, then run locally";
  `install_unraid.sh` now copies its own bundled overlay files instead of trying
  to download them. `my-book-translator-api.xml` pointed `Repository` at
  `ghcr.io/username/book-translator-api:latest` — never published, contradicting
  every other deploy path in the repo (which all build `local/book-translator-api`
  locally). Fixed, and de-duplicated: `install_unraid.sh` had a second, drifted
  copy of this XML embedded inline (how the `ghcr.io` bug happened in the first
  place) — it now copies the one canonical file.
- **Portability:** `benchmark.py` / `benchmark_realistic.py` / `test_endpoints.py`
  / `test_ratelimit.py` hardcoded a specific homelab LAN IP; switched to a
  `BENCHMARK_URL` env var (default `127.0.0.1:8390`). `test_endpoints.py` also
  called a `/prefetch` route that no longer exists (404) — replaced with `/ping`.
  `deploy_unraid.sh` / `verify_unraid.sh` labeled as personal example scripts,
  host/user now overridable via env vars instead of hardcoded.
- **Docs/metadata:** removed fabricated benchmark numbers from the README,
  replaced with instructions to run the included benchmark scripts against your
  own deployment. `package.json` license `ISC` → `MIT` (matches `LICENSE`),
  version synced, author/copyright name filled in. Added
  `docs/DEVELOPMENT.md` test-matrix instructions and
  `.github/workflows/ci.yml` running the existing self-contained suites on
  push/PR (Gitea Actions is enabled on this repo).

Verified: `py_compile` on every changed `.py`, `node -c`, full backend suite
(18/18) and the jsdom frontend suite still pass, every shell script passes
`bash -n`, the XML template parses, and a real isolated Docker build/run/stop
on the homelab (separate tag/port/container, fully cleaned up) confirmed
`PORT` + `/ping` + graceful shutdown work end-to-end.

## [1.3.0] - 2026-06-30
UI version marker: `2026-06-30-speed-profile-v1`.

### Changed
- **Proportional output-token cap.** `max_tokens` per LLM request is now scaled to
  the input size (`input_tokens × BT_OUTPUT_TOKEN_FACTOR + BT_OUTPUT_TOKEN_FLOOR`,
  clamped to the existing ceilings) instead of always sending 4096/8192. This stops
  a rambling/stuck local model from generating thousands of tokens for a short
  paragraph — the main driver of the 8–20s cold latencies and 120s vLLM read
  timeouts — without truncating legitimate translations (factor 2.0 is generous).
- New env vars `BT_OUTPUT_TOKEN_FACTOR` (default `2.0`) and `BT_OUTPUT_TOKEN_FLOOR`
  (default `256`). Backend-only change; tests cover the cap (proportional, clamped,
  floored, monotonic).

### Fixed
- **False "unhealthy" container.** The Docker healthcheck hit `/health`, which runs a
  real LLM generation probe; while vLLM is busy that probe queues and exceeds the 4s
  healthcheck timeout, so the container was flagged unhealthy even though it was
  serving translations. Added a lightweight `/ping` endpoint (no LLM) and pointed the
  healthcheck at it; `/health` stays as the deep probe for humans.

### Deployed (Unraid, 2026-06-30)
- Verified-stable runtime env: `BT_BATCH_SIZE=3`, `BT_BATCH_MAX_TOKENS=1200`,
  `BT_MAX_TOKENS=640`, `BT_TIMEOUT=60`, `BT_MAX_CONCURRENT=1`, `BT_CONTEXT_WINDOW=1`.
  These keep each vLLM call short enough to finish within the timeout under ~8-way
  contention, preventing the "all slots stuck generating to max_tokens" runaway that
  was making cold translations time out. `deploy_unraid.sh` updated to these values
  and to the correct repo path (`/mnt/user/appdata/book-translator-api`).
  Result: cold single-paragraph ~0.3s, 5-paragraph batch ~2.5s server-side.

## [1.2.1] - 2026-06-30
UI version marker: `2026-06-30-rate-limit-backoff-v1` (backend/frontend rate limit improvements).

### Added
- **Frontend translation queue**: All translation requests now go through a single scheduler to enforce `BT_CLIENT_MAX_INFLIGHT` (default 1) and pause background prefetching during active page translation.
- **Graceful Rate Limiting**: Frontend now parses `Retry-After` JSON and headers. If the backend hits the 429 rate limit, the frontend pauses its queue without showing a fatal error, updates the UI status to "Rate limited — waiting Ns...", and resumes seamlessly.
- **Backend JSON 429 response**: The rate limiter now returns `{"error": "rate_limited", "retry_after": N}` along with `Retry-After` HTTP headers.
- **Rate Limit Environment Variables**: `BT_RATE_LIMIT_PER_MINUTE` (default 120) and `BT_RATE_LIMIT_RETRY_AFTER` (default 10) configurable limits.

## [1.2.0] - 2026-06-30
UI version marker: `2026-06-30-ui-polish-v1`.

### Fixed
- **Settings gear opened no visible menu.** The popover was a child of the control
  bar, which uses `overflow: hidden` to clip the progress bar — so the menu (drawn
  above the bar) was clipped away. The menu is now a body-level fixed popover.
- **Bilingual translation looked "glued" to the original and had no colour.** Parent-page
  CSS does not cascade into the EPUB.js iframe, so the `.bt-translation` class was unstyled
  inside the reader. The plugin now injects its translation stylesheet directly into the
  iframe document (with light/dark/sepia theme detection), restoring clear spacing, a blue
  tint, a left border, and a subtle background — all theme-safe via CSS variables.
- **Headings/subtitles** ("Chapter Two", section titles, centered epigraphs/quotes) are now
  reliably translated and rendered with a dedicated `.bt-heading-translation` style (centered
  when the original is centered) instead of being glued to the original.

### Changed
- `getTranslatableElements(doc)` is the canonical selector: adds `blockquote` and
  `epigraph`/`quote`/`verse` classes, excludes plugin UI (`#bt-bar`/`#bt-menu`/`#bt-toast`,
  `.bt-translation`, `.bt-loading`), preserves standalone TOC links and their `href`.
- Settings menu now shows: header + UI version, current mode and target language, a
  persisted background-prefetch toggle, a "retry current page" action, cache-clear actions,
  and live debug info (queue length, generation, last trigger reason). Closes on outside
  click and Escape.
- Bilingual rendering is idempotent: it restores inline-replaced text before inserting,
  updates the existing translation child instead of duplicating, and survives 10+ mode
  cycles / page turns / chapter changes without stacking blocks.

## [1.1.1] - 2026-06-30
### Fixed
- **Deployment sync (Unraid):** CWA container was serving the old bundled `translator.js` instead
  of the overlay files because the container lacked file-level bind mounts. Container recreated
  with the correct mounts from the Unraid XML template. Overlay and container now serve identical
  files (verified by SHA-256 hash match).
- **Version marker bumped to `2026-06-30-opus-deploy-sync-v1`** — a brief toast is shown on load
  so users can confirm the correct JS version is running without opening DevTools.
- **Cache-busting query strings** added to `read.html` asset URLs
  (`?v=2026-06-30-opus-deploy-sync-v1`) so browser caches are bypassed after upgrades.

### Documentation
- Rewrote `docs/DEPLOY_UNRAID.md` to document the real architecture: file bind mounts via the
  Unraid XML template, the `/mnt/user/appdata/calibre-web-automated/overlay/` deploy target, and
  why `docker restart` alone is insufficient if template mounts change.
- Rewrote `docs/TROUBLESHOOTING.md` with verified steps for every known issue.

## [1.1.0] - 2026-06-30
### Added
- **Context-Aware Translation (`BT_CONTEXT_WINDOW`, default 0):** option to send previous/next paragraphs to the LLM to improve literary quality and pronoun accuracy.
- **Unraid deployment & verification automation:** created `deploy_unraid.sh` and `verify_unraid.sh` for safe script and backend upgrades with automatic backups.
- **Build/Version indicator:** Version `2026-06-30-chapter-auto-v1` logged to console and displayed in the settings menu.

### Fixed
- **Chapter-Change Auto-Translation:** Resolved bug where navigating from chapter 1 to chapter 2 sometimes didn't auto-translate. Built a unified `scheduleTranslate` debouncing strategy and iframe document identity tracking.
- **UI status messages:** Unified and improved status text (`✓ Ready`, `Preparing next text…`) adapting cleanly to dark/sepia themes.

## Legacy notes retained from the initial 1.x development log
### Added
- Multi-provider support directly from environment variables.
- Integration for OpenRouter and DeepSeek via standard `requests`.
- `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY` environment variables.
- Optional fallback provider (`LLM_FALLBACK_PROVIDER` / `_MODEL` / `_API_KEY`).
- Tunables `BT_TIMEOUT` and `BT_MAX_CONCURRENT` (default 2) for slow local models.
- **Batched-prompt translation (`BT_BATCH_SIZE`, default 5):** several paragraphs
  are translated in a single LLM call — dramatically faster on slow local models —
  with a transparent per-paragraph fallback if the segmented reply can't be parsed.
- **Optional API auth (`BT_API_TOKEN` + `X-BT-Token`)** for setups exposed beyond the LAN.
- Docker `HEALTHCHECK` hitting `/health`.
- Self-contained backend test (`test_translation.py`) using a mocked LLM — no live server.
- Standardized GitHub/Gitea templates and community health files.
- **Reworked control bar (bottom-center):** live status with spinner, a chapter
  progress bar + `done/total` counter, a `✓ Done` state, and a clickable
  `⚠ Error — retry` state.
- **Settings menu (⚙):** toggle whole-chapter pre-translation, clear this
  language's cache, clear all cache, and a cached-entry count.
- **Persistent client cache** in `localStorage` per language (survives page
  turns and reloads); switching language restores that language's work.
- `Alt+T` keyboard shortcut to cycle translation mode.

### Changed
- Refactored `translator.py` architecture to use `requests` over `urllib`.
- Swapped background `_translate_paragraphs` processing to use concurrent `ThreadPoolExecutor` fetching.
- Upgraded default Gunicorn execution strategy to `1 worker / 8 threads` to prevent memory drift across application states.
- **Visible-first translation:** the on-screen page is translated one paragraph
  at a time and painted progressively; the rest of the chapter fills in afterward
  as a low-priority, preemptible background pass that pauses for the visible page.
- Page/chapter turns now preempt stale prefetch and auto-translate the new page.
- Control-bar styling moved into `translator.css` (class-driven); translation
  text now inherits the reader's light/dark/sepia theme instead of a hardcoded colour.

### Fixed
- Translation errors/empties are no longer shown as stuck text nor cached
  client-side, so transient local-LLM failures retry instead of sticking.
- Client paragraph hashing upgraded from 32-bit to a 53-bit hash (cyrb53) to
  avoid collisions showing the wrong cached translation in long books.
- `getParagraphs()` ancestor de-dup is now O(n) via a Set (was O(n²) — janky on
  big chapters); `getVisibleParagraphs()` reuses that same canonical set.
- Cache DB collisions (Database is Locked) resolved by enabling `PRAGMA busy_timeout=5000` and minimizing read locking.
- Resolved memory leakage in Rate Limiter dictionary by implementing an hourly background cleaner.
- Mitigated negative JS hash generation limits by enforcing strict unsigned zero-shifted bits.
- `docker-compose` injected the JS/CSS from `./static` (was a non-existent `./overlay` path);
  added `host.docker.internal` + `BT_LOCAL_URL` so `provider=local` reaches the host LLM.

## [1.0.0] - 2026-06-25
### Added
- Initial bilingual translation overlay release.
- SQLite SHA-256 fallback cache system.
- Light/Dark mode integration with CWA internal iframe rendering.
- `translator.js` client logic for dynamic DOM injection.
