# Troubleshooting Guide

---

## "XML Parsing Error: not well-formed" with garbage characters on every page

The book is **DRM-protected** (usually Adobe ADEPT). Check the epub for a
`META-INF/encryption.xml` entry:

```bash
unzip -l "book.epub" | grep encryption.xml
```

If present, the chapter files inside the epub are encrypted; CWA's web reader
(and therefore this plugin) cannot display them — the browser receives
ciphertext and fails to parse it. This is a property of the file, not a plugin
bug. Only DRM-free epubs are supported.

---

## Control bar missing after switching to proxy-injection mode

1. Make sure you're reading through the **proxy port** (or a domain/reverse
   proxy that points at it) — CWA's own port serves stock CWA with no overlay.
2. Hard-refresh once (`Ctrl+Shift+R`) to drop the previously cached reader page.
3. Confirm injection: `curl -s http://<host>:<proxy-port>/login | grep loader.js`
   should print the loader tag with the current version.

---

## 502/504 through the proxy after restarting only the CWA container

nginx resolves `CWA_UPSTREAM` when the proxy role starts. If you recreate CWA
and its container IP changes, restart `book-translator-proxy` too. Restarting
the Compose project also refreshes both endpoints.

---

## How to confirm the right frontend version is running

Open browser DevTools (F12), go to **Console** tab, then:
- Open an EPUB in CWA.
- You should see: `[BookTranslator] loaded version <date>-<descriptor>-v<N>`
- A version toast will appear briefly in the UI.

Check what version your checkout/deployment is actually on (don't hardcode a
specific version string here — see `CHANGELOG.md` for the current one):
```bash
# What the repo/overlay file says:
grep -n "BT_UI_VERSION" /mnt/user/appdata/calibre-web-automated/overlay/translator.js
# What's actually live inside the CWA container:
docker exec calibre-web-automated grep -n "BT_UI_VERSION" /app/calibre-web-automated/cps/static/js/translator.js
# Both lines must match, and must match the latest entry in CHANGELOG.md.
```

---

## 1. Button visible but no translation

- **API readiness**: `curl -s http://127.0.0.1:8390/health` — must show
  `"status":"ok"`. This is a shallow check; `/ping` is liveness-only and
  neither endpoint contacts the LLM. To diagnose the configured provider, call
  `/health/deep` with `X-BT-Token` set to `BT_API_TOKEN`, or to the persisted
  `/app/data/cleanup_token` when no API token is configured.
- **`BT_LOCAL_URL` is wrong**: Inside Docker, `localhost` means the container, not the host.
  Use your host's LAN IP (or `host.docker.internal`), e.g.
  `BT_LOCAL_URL=http://192.168.1.x:8000/v1/chat/completions`.
- **Browser console**: Open DevTools → Network tab → look for a failing POST to `/translate/batch`.
- **Target language wrong**: If you are reading a Spanish book and target is also Spanish,
  the backend skips translation (source == target). Change target language in the UI.

---

## 2. Frequent "Rate limited — waiting Ns…" messages

- **Backend limit reached**: You are hitting the global request limit per IP.
  - The default limit is 120 requests per minute (`BT_RATE_LIMIT_PER_MINUTE`).
  - Increase this via the `BT_RATE_LIMIT_PER_MINUTE` environment variable on the server.
- **Aggressive frontend prefetch**: The frontend defaults to requesting chapter paragraphs
  progressively. To slow it down, increase `BT_CLIENT_MIN_REQUEST_GAP_MS`.

---

## 3. Spinner / progress indicator missing, invisible, or jittery

- **Old JS cached**: Hard refresh the reader page (`Ctrl+Shift+R` / `Cmd+Shift+R`).
- **Wrong version deployed**: Check that `BT_UI_VERSION` in the container matches the overlay:
  ```bash
  sha256sum /mnt/user/appdata/calibre-web-automated/overlay/translator.js
  docker exec calibre-web-automated sha256sum /app/calibre-web-automated/cps/static/js/translator.js
  # Both hashes must be identical.
  ```
- **File bind mounts missing**: If they differ, the container lacks the file-level bind mounts.
  Recreate the container following `docs/DEPLOY_UNRAID.md`.
- **Jittery "wide/narrow" indeterminate fill** (fixed in `2026-06-30-progress-bar-fix-v1`):
  earlier versions slid a fixed-width fill block across the pill, which got visually
  clipped by the rounded corners and looked like fast, distracting width jitter. If you
  still see this, you're on an older cached/deployed JS — see "confirm the right
  frontend version" above.

---

## 4. Page / chapter change stops translating (requires button toggle)

- **Old JS**: Confirm `BT_UI_VERSION` matches the latest `CHANGELOG.md` entry (see top of
  this doc).
- **Iframe not detected**: Viewer uses epub.js inside an `<iframe>`. Open DevTools console,
  look for `scheduleTranslate(reason=...)` log lines. If absent, iframe detection failed.
- **epub.js hooks**: The `attachEpubHooks` function retries for `window.reader.rendition`
  up to several times. On very slow devices the EPUB may not be ready before this gives up.
  Reload the page.
- **Generation counter**: Each page/chapter turn increments `generation`. Stale fetches from
  the old page are discarded; fresh ones start automatically. If it's still not working,
  check the console for `AbortError` messages.

---

## 5. Duplicate bilingual blocks

- You are running two versions of translator.js simultaneously (iframe + parent doc).
  Check that only ONE `<script src="...translator.js">` exists in the rendered HTML.
- Clear browser localStorage translation cache via the ⚙️ menu → "Clear all cache".

---

## 6. Wrong `BT_LOCAL_URL`

The path is always `/v1/chat/completions` (vLLM, LM Studio, Ollama, llama.cpp all speak
it) — only host:port changes. Typical ports: vLLM `:8000`, LM Studio `:1234`, Ollama
`:11434`. Using `http://localhost:.../...` from inside Docker means the API container
talks to itself — no LLM lives there; use the host's LAN IP or `host.docker.internal`.

To fix without rebuilding the image:
```bash
docker stop book-translator-api && docker rm book-translator-api
docker run -d --name book-translator-api ... -e BT_LOCAL_URL=http://<your-host-ip>:<port>/v1/chat/completions ...
```

---

## 7. Frontend files deployed but container serves old version

This happens when the container was started **before** the file bind mounts were added to the
Unraid XML template, or was restarted with `docker restart` instead of being recreated.

**How to fix**: Stop, remove, and recreate the container so it picks up the file mounts:
```bash
docker stop calibre-web-automated && docker rm calibre-web-automated
# Then run with file mounts — see docs/DEPLOY_UNRAID.md
```

After recreation, verify:
```bash
docker inspect calibre-web-automated --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}"
# Must include:
# /mnt/user/appdata/calibre-web-automated/overlay/translator.js -> /app/.../translator.js
```

---

## 8. CWA container crashes or won't start after recreation

- The `cwa-init` service may fail if it can't write to a read-only bind mount.
  Our overlay files are mounted `ro` — this is intentional for the plugin files only.
- If CWA crashes, check: `docker logs calibre-web-automated 2>&1 | tail -50`
- If `cwa-init` complains about file ownership, add `NETWORK_SHARE_MODE=true` env.

---

## 9. Settings gear (⚙) does nothing

- **Fixed in `2026-06-30-ui-polish-v1`.** The menu was being clipped by the control
  bar's `overflow: hidden`; it is now a body-level popover anchored above the pill.
- If the gear still seems dead, you are running an **older cached JS** — hard refresh
  (`Ctrl+Shift+R`) and check the current `BT_UI_VERSION` (see top of this doc).
- The menu closes on a click outside it or on `Escape`. It shows the UI version, current
  mode/language, a background-prefetch toggle, retry, cache-clear actions, and debug info.

---

## 10. Bilingual translation is glued to the original / hard to read

- **Fixed in `2026-06-30-ui-polish-v1`.** Parent-page CSS does not reach inside the
  EPUB.js `<iframe>`, so the translation styles are now injected directly into the reader
  document. The translated line appears under the original with spacing, a blue tint, a
  left border, and a faint background.
- Theme-safe: the blue adapts to white / dark / sepia readers automatically (the plugin
  measures the reader background and sets `data-bt-theme` on the iframe `<html>`).
- If translations look unstyled, the injected `<style id="bt-injected-styles">` may have
  failed — reload the page so the iframe is re-detected.

---

## 11. Some headings / subtitles aren't translated, or are glued

- **Improved in `2026-06-30-ui-polish-v1`.** The selector now covers `h1`–`h6`,
  `blockquote`, and `title`/`subtitle`/`chapter`/`heading`/`epigraph`/`quote` classes,
  and headings render with a dedicated, spaced, centered-when-appropriate style.
- If a specific heading still isn't picked up, it likely uses an unusual class/structure —
  note the element (DevTools → Inspect) so the selector can be extended.

---

## 12. Live API server / benchmark scripts fail with a connection error

- `test_endpoints.py`, `test_ratelimit.py`, `benchmark.py`, and `benchmark_realistic.py`
  all hit a **live** server, not a mock — start the API first.
- They read the target from `BENCHMARK_URL` (default `http://127.0.0.1:8390`):
  ```bash
  BENCHMARK_URL=http://192.168.1.x:8390 python benchmark.py
  ```
- For mocked tests with no live server required, use `test_translation.py` instead.
