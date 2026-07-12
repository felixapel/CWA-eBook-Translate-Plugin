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
   python3 server.py
   ```

### Running Tests

The backend test suite is self-contained — it mocks the LLM and the database
file, so it needs no running server, no API key, and no network access:
```bash
.venv/bin/python3 test_translation.py
.venv/bin/python3 -m unittest -v test_cache_v2 test_context_cache test_singleflight
```

Always also check syntax/compile before committing:
```bash
python3 -m py_compile server.py translator.py cache.py singleflight.py work_budget.py
```

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

For the recommended CWA-session proxy, pass the browser cookie through an
environment variable rather than the command line:

```bash
BT_RATE_LIMIT_TEST_COOKIE='session=<opaque-value>' \
  python3 test_ratelimit.py --url http://127.0.0.1:8080/bt-api
```

It exits nonzero on connection/authentication errors, unexpected statuses, or
if it does not observe both an admitted request and a `429` response.

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
