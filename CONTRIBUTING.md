# Contributing to CWA eBook Translate

Thanks for helping improve the project. Keep changes narrow, explain the user
problem, and include a regression test for behavior changes.

## Development setup

1. Fork and clone the repository, then create a short-lived branch.
2. Install Python 3.11 and Node from `.node-version`.
3. Create a virtual environment and install the reviewed lock:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
   npm ci
   ```

Tests use fakes and do not need an LLM key. Keep real credentials in a private
environment file outside the checkout; never commit `.env`, cookies, book text,
provider keys, or generated deployment state.

## Required checks

Run the smallest relevant test while developing, then the maintained gates
before opening a pull request:

```bash
python3 -m unittest -v test_live_scripts test_install_docs
python3 test_translation.py
python3 test_hardening.py
python3 -m unittest discover -v
node -c static/translator.js
node -c static/loader.js
npm ci
npm audit --audit-level=high
npm test
npx playwright install --with-deps --only-shell chromium
npm run test:e2e
```

Container or installer changes must also run the applicable smoke commands in
`docs/RELEASE.md`. Live benchmark scripts require explicit authentication; see
`docs/DEVELOPMENT.md`.

Dependency updates must change the relevant `requirements*.in` file, regenerate
the committed hash lock with `scripts/compile-requirements.sh`, and include the
reviewed diff. Do not hand-edit generated requirement locks.

## Pull requests

- Explain the problem, approach, risk, and verification evidence.
- State the exact tag or commit used for runtime reproductions.
- Add or update tests before changing behavior.
- Keep frontend changes compatible with light, dark, sepia, and black reader
  themes and exercise the real Chromium suite.
- Do not include raw `/metrics`, logs, cookies, paths, book text, or provider
  responses without redacting private data.

For bugs, use the issue template and begin with `./btctl doctor --json` on
managed installs. Security reports belong in the private channel described in
`SECURITY.md`, never a public issue.
