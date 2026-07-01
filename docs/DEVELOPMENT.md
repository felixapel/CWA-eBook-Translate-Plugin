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
   pip install -r requirements.txt
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
```

Always also check syntax/compile before committing:
```bash
python3 -m py_compile server.py translator.py cache.py
```

`test_endpoints.py`, `test_ratelimit.py`, `benchmark.py`, and
`benchmark_realistic.py` are different — they hit a **live** API
(`BENCHMARK_URL`, default `http://127.0.0.1:8390`), so start the server first:
```bash
python3 server.py &
BENCHMARK_URL=http://127.0.0.1:8390 python3 test_endpoints.py
```

## Frontend Development

The frontend consists of `static/translator.js`, `static/translator.css`, and
`overlay/read.html`.

### Syntax Validation & Tests

```bash
node -c static/translator.js   # syntax check
npm install                    # pulls jsdom (devDependency)
npm test                       # runs test_frontend.js against a mocked reader/iframe
```

### Manual Testing

The DOM-injection logic only fully exercises against the real EPUB.js reader.
After any change to `getTranslatableElements`, paragraph detection, or
rendering, manually verify in a browser: open an EPUB in CWA, cycle
Original → Bilingual → Translated, change chapters/pages, and check Light /
Dark / Sepia themes (translation styling is injected into the reader
`<iframe>` — see `ensureIframeStyles` in `translator.js`).
