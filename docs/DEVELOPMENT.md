# Development Guide

This guide details how to work on the `book-translator` codebase.

## Backend Development

The backend is a Flask application running in python. 

### Local Setup

1. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
   ```
3. Run the development server:
   ```bash
   BT_AUTH_MODE=token BT_API_TOKEN=local-development-only python3 server.py
   ```

### Running Tests

The backend test suite is self-contained — it mocks the LLM and the database
file, so it needs no running server, no API key, and no network access:
```bash
.venv/bin/python3 test_translation.py
.venv/bin/python3 test_hardening.py
.venv/bin/python3 -m unittest -v \
  test_btctl test_btctl_container test_btctl_compose test_btctl_unraid test_btctl_auth \
  test_btctl_lifecycle test_work_budget test_provider_budget test_cache_v2 \
  test_context_cache test_singleflight test_auth test_ci_contract \
  test_install_docs test_release_contract test_supply_chain_contract \
  test_shell_contract test_container_contract test_cleanup_token \
  test_api_schema test_error_privacy test_observability test_proxy_config \
  test_live_scripts
```

Always also check syntax/compile before committing:
```bash
python3 -m py_compile btctl.py btctl_container.py btctl_core.py \
  btctl_compose.py btctl_docker.py btctl_paths.py btctl_unraid.py btctl_auth.py \
  btctl_lifecycle.py auth.py server.py \
  translator.py cache.py singleflight.py work_budget.py proxy/render_config.py
bash -n btctl scripts/*.sh
```

The installer contract is self-contained and uses disposable Git repositories;
it never contacts Docker or a live CWA instance:

```bash
python3 -m unittest -v test_btctl test_btctl_container test_btctl_compose test_btctl_unraid \
  test_btctl_auth test_btctl_lifecycle test_install_docs
```

Use `./btctl plan --env /absolute/path/install.env --json` to inspect a clean
checkout. `btctl.py` is the internal Python entry point; operator documentation
and integration tests must use the public `./btctl` dispatcher. Its stock-Unraid
fallback may build temporary helper images and warm Docker cache, but `plan`
must not change deployment files, state, CWA, or running containers.

The real-Docker regression for a host with no Python is:

```bash
./scripts/btctl-bootstrap-smoke.sh "btctl-bootstrap-$(git rev-parse --short=12 HEAD)"
```

It runs the public dispatcher in a clean checkout, removes Python and Git from
the simulated host image, verifies that `plan --json` reports the exact commit,
and exercises Unraid install, doctor, and uninstall through the same fallback.

`test_endpoints.py`, `test_ratelimit.py`, `benchmark.py`, and
`benchmark_realistic.py` are different — they hit a **live** API
(`BENCHMARK_URL`, default `http://127.0.0.1:8390`), so start the server first:
```bash
python3 server.py &
BENCHMARK_URL=http://127.0.0.1:8390 python3 test_endpoints.py
```

The rate-limit probe requires a fresh limiter window and valid authentication.
It sends same-language requests, so it exercises authentication and admission
without calling the configured translation provider. Its default 130 probes
cover the default limit of 120; raise `--requests` if your deployment uses a
higher `BT_RATE_LIMIT_PER_MINUTE`:

```bash
BT_API_TOKEN='<token>' python3 test_ratelimit.py \
  --url http://127.0.0.1:8390 --requests 130 --timeout 5
```

For the recommended CWA-session proxy, pass the browser cookie and exact
login-time User-Agent through environment variables rather than the command
line, and run the probe from the same client IP that created the session:

```bash
BT_RATE_LIMIT_TEST_COOKIE='session=<opaque-value>' \
BT_RATE_LIMIT_TEST_USER_AGENT='Mozilla/5.0 ... exact browser value' \
  python3 test_ratelimit.py --url https://books.example.test/bt-api
```

It exits nonzero on connection/authentication errors, unexpected statuses, or
if it does not observe both an admitted request and a `429` response. The probe
ignores inherited `HTTP_PROXY`/`HTTPS_PROXY` settings, refuses redirects and URL
credentials, streams no response body, and closes each response immediately so
the token or CWA cookie stays bound to the explicitly selected origin.

The two benchmark scripts enforce the same boundary and also fail on redirects,
non-2xx responses, or invalid JSON. Use one authentication mechanism only:

```bash
BT_API_TOKEN='<token>' python3 benchmark.py \
  --url http://127.0.0.1:8390
BT_BENCHMARK_COOKIE='session=<opaque-value>' \
BT_BENCHMARK_USER_AGENT='Mozilla/5.0 ... exact browser value' \
  python3 benchmark_realistic.py \
  --url https://books.example.test/bt-api
```

Do not paste credentials into a URL or publish benchmark output containing
private endpoint names. CWA strong sessions bind the cookie to the browser
User-Agent and observed source address; a mismatched live probe can invalidate
the session, so sign in again if either value was wrong.

## Frontend Development

The frontend consists of `static/translator.js`, `static/translator.css`, and
`overlay/read.html`. CI reads the exact supported LTS release from
`.node-version`; use the same version locally.

### Syntax Validation & Tests

```bash
node -c static/translator.js   # syntax check
npm ci                         # exact package-lock.json dependency tree
npm test                       # runs test_frontend.js against a mocked reader/iframe
npx playwright install --with-deps --only-shell chromium
npm run test:e2e               # real Chromium: loader, DOM, network, a11y, consent
```

The browser suite starts a localhost-only CWA reader fixture, intercepts only
its `/bt-api/translate/batch` route, and fails on browser console errors,
warnings, page exceptions, or failed requests. To reuse a compatible local
Chromium instead of Playwright's managed headless shell, set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/absolute/path/to/chromium`.

## Updating Dependency Locks

`requirements.in` records runtime intent. `requirements.txt` is the reviewed
production lock; every direct and transitive dependency is version-pinned and
hashed. The auditor and lock compiler have independent locks so CI does not
resolve mutable tooling at runtime.

Regenerate all three locks with the currently approved compiler:

```bash
python3.11 -m venv /tmp/cwa-lock-tools
/tmp/cwa-lock-tools/bin/python -m pip install \
  --require-hashes --only-binary=:all: -r requirements-compile.txt
LOCK_PYTHON=/tmp/cwa-lock-tools/bin/python \
  ./scripts/compile-requirements.sh
git diff -- requirements.txt requirements-audit.txt requirements-compile.txt
```

Review every version and hash change, run the complete test/container gate, and
commit the `.in` file and its generated lock together. To run dependency audits
locally, install `requirements-audit.txt` with the same two pip safety flags and
then run `./scripts/audit-deps.sh`.

### Manual Testing

The automated Chromium gate covers loader isolation, the reader iframe,
translation rendering, cloud-fallback consent, and the control accessibility
tree. CWA/EPUB.js compatibility and theme integration still require the real
application.

After any change to `getTranslatableElements`, paragraph detection, or
rendering, manually verify in a browser: open an EPUB in CWA, cycle
Original → Bilingual → Translated, change chapters/pages, and check Light /
Dark / Sepia themes (translation styling is injected into the reader
`<iframe>` — see `ensureIframeStyles` in `translator.js`).
