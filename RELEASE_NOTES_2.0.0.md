# Release notes — v2.0.0 (production-ready)

This is the first release I'd consider safe to point at Felix's real library.
It bundles the four production-readiness fixes from PR #10 (cache size
accounting, source==target short-circuit, /stats observability, model-scoped
cache key) and the eleven production-hardening fixes from PR #11 (non-root
container, request cap, proxy allowlist, cleanup auth fail-safe, IP hygiene).

## What's in this release

### Bug fixes (from PR #10)

- `db_size_mb` now reports the real on-disk footprint, including WAL and
  SHM siblings. Operators relying on the value for backup sizing or
  cleanup triggers were seeing `0.0` when the cache had thousands of rows.
- `source_lang == target_lang` short-circuits before any LLM call.
  Translating a Spanish book to "Spanish" no longer spends a paid call
  or pollutes the cache with `Spanish→Spanish` self-pairs.
- `/stats` is exempt from rate limiting. Operators monitoring a
  deployment under attack can poll it even while the per-client budget
  is exhausted.
- The cache key is now scoped by model. Switching `LLM_MODEL` or
  `LLM_PROVIDER` no longer silently serves translations from the
  previous backend. The hardcoded `'MiniMax-M3'` default that leaked
  provider branding is gone; `put_cache` now requires a model name.

### Hardening (from PR #11)

- The gunicorn process runs as `appuser` (system account, no shell, no
  password). nginx keeps root because it needs the listen port and log
  dirs; the split is documented in the Dockerfile comment.
- `MAX_CONTENT_LENGTH` is now set globally (2 MB default, env-overridable)
  with a JSON `errorhandler(413)`. A 10 MB body is rejected before Flask
  even parses it; the per-field caps in the route handlers remain the
  second backstop.
- `/cache/cleanup` is always auth-gated. When `BT_API_TOKEN` is unset
  (the common self-host case), the endpoint auto-generates a
  `secrets.token_urlsafe(32)` token, persists it to
  `BT_CACHE_DIR/cleanup_token` (mode 0600, owned by `appuser`), and logs
  it once at WARNING. Operators read it from `docker logs` or set
  `BT_API_TOKEN` to silence.
- New `BT_TRUSTED_PROXIES` env var: a comma-separated allowlist of CIDRs
  or IPs the *peer* must match before `X-Forwarded-For` is honored.
  Closes a rate-limit bypass where a direct client could spoof XFF and
  pool every request into a single bucket. The legacy `BT_TRUST_PROXY`
  boolean still works but is now marked dev-only in the README.
- CI now runs `pip-audit` and `npm audit` on every push and PR. The
  backend job builds the Docker image, runs it, and verifies the
  gunicorn process runs as `appuser` so the non-root fix cannot
  regress silently. No CVEs in either dependency surface at release time.
- All hardcoded private LAN IPs (`192.168.0.x`) in `deploy_unraid.sh`,
  `verify_unraid.sh`, `docs/DEPLOY_UNRAID.md`, and the README nginx
  example have been replaced with placeholder values (`10.0.0.10`,
  `10.0.0.20`). The deploy script now derives the LLM host from a
  `LLM_HOST` env var. **This is what unblocks mirroring the repo
  publicly without leaking the home network topology.**

## Upgrade notes

No data migrations. No breaking changes.

- New env vars are additive: `BT_MAX_CONTENT_LENGTH` (default 2 MB,
  env-overridable), `BT_TRUSTED_PROXIES` (empty default = behaviour
  unchanged).
- The cleanup-token fail-safe requires `BT_CACHE_DIR` (or the implicit
  `/app/data` default) to be writable by the gunicorn process. The
  provided Dockerfile already has this right.
- The B4 cache-key change (from PR #10) is non-destructive: existing
  rows will simply miss once the new key is in effect; the cache
  re-warms gradually. Operators may see a temporary uptick in
  `cache_misses` until the cache rebuilds.
- Operators behind a reverse proxy should switch from
  `BT_TRUST_PROXY=true` to `BT_TRUSTED_PROXIES=<proxy-IP-or-CIDR>`.
  The legacy flag still works but is now marked dev-only.

## Verification (run on this release's HEAD)

- `python3 test_translation.py` — 38 assertions, all pass
- `python3 test_hardening.py` — 17 assertions, all pass
- Runtime smoke in a real container:
  - `/ping` returns 200 with `{"status":"ok"}`
  - gunicorn process runs as `appuser`, not root
  - `/app/data/cleanup_token` is mode 0600, owned by `appuser`
  - `/cache/cleanup` with no header: 401
  - `/cache/cleanup` with bogus header: 401
  - `/cache/cleanup` with the auto-gen token from `docker logs`: 200
  - 3 MB body to `/translate`: 413 with JSON body
- `pip-audit -r requirements.txt --strict`: 0 vulnerabilities
- `npm audit --omit=dev`: 0 vulnerabilities

## Credits

The fixes in PR #10 were identified by a dynamic, runtime-based audit
of every endpoint (live gunicorn + Docker container + 1000-request
stress test) on 2026-07-02. The hardening in PR #11 was identified
by static read, runtime smoke testing, and dependency audit on the
same day. Both PRs are authored by hermes (the author's AI
collaborator) under the repo's standard contribution model.
