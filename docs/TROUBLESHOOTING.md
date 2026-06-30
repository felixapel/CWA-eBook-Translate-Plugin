# Troubleshooting Guide

---

## How to confirm the right frontend version is running

Open browser DevTools (F12), go to **Console** tab, then:
- Open an EPUB in CWA at `http://192.168.0.122:8383`.
- You should see: `[BookTranslator] loaded version 2026-06-30-ui-polish-v1`
- A version toast will appear briefly in the UI.

To grep version on the server:
```bash
grep -n "BT_UI_VERSION" /mnt/user/appdata/calibre-web-automated/overlay/translator.js
# Then confirm the same version is live in the container:
docker exec calibre-web-automated grep -n "BT_UI_VERSION" /app/calibre-web-automated/cps/static/js/translator.js
```

---

## 1. Button visible but no translation

- **API health**: `curl -s http://192.168.0.122:8390/health` — must show `"status":"ok"`.
- **BT_LOCAL_URL is wrong**: Inside Docker, `localhost` means the container, not the host.
  Use: `BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions`
- **Browser console**: Open DevTools → Network tab → look for a failing POST to `/translate/batch`.
- **Target language wrong**: If you are reading a Spanish book and target is also Spanish,
  the backend skips translation (source == target). Change target language in the UI.

---

## 2. Spinner / progress bar missing or invisible

- **Old JS cached**: Hard refresh the reader page (`Ctrl+Shift+R` / `Cmd+Shift+R`).
- **Wrong version deployed**: Check that `BT_UI_VERSION` in the container matches the overlay:
  ```bash
  sha256sum /mnt/user/appdata/calibre-web-automated/overlay/translator.js
  docker exec calibre-web-automated sha256sum /app/calibre-web-automated/cps/static/js/translator.js
  # Both hashes must be identical.
  ```
- **File bind mounts missing**: If they differ, the container lacks the file-level bind mounts.
  Recreate the container following `docs/DEPLOY_UNRAID.md`.

---

## 3. Page / chapter change stops translating (requires button toggle)

- **Old JS**: Ensure `BT_UI_VERSION = '2026-06-30-ui-polish-v1'` or newer.
- **Iframe not detected**: Viewer uses epub.js inside an `<iframe>`. Open DevTools console,
  look for `scheduleTranslate(reason=...)` log lines. If absent, iframe detection failed.
- **epub.js hooks**: The `attachEpubHooks` function retries for `window.reader.rendition`
  up to several times. On very slow devices the EPUB may not be ready before this gives up.
  Reload the page.
- **Generation counter**: Each page/chapter turn increments `generation`. Stale fetches from
  the old page are discarded; fresh ones start automatically. If it's still not working,
  check the console for `AbortError` messages.

---

## 4. Duplicate bilingual blocks

- You are running two versions of translator.js simultaneously (iframe + parent doc).
  Check that only ONE `<script src="...translator.js">` exists in the rendered HTML.
- Clear browser localStorage translation cache via the ⚙️ menu → "Clear all cache".

---

## 5. Wrong BT_LOCAL_URL

| LLM Backend | Correct URL |
|-------------|-------------|
| vLLM (FelixServer) | `http://192.168.0.122:2819/v1/chat/completions` |
| Ollama (FelixServer) | `http://192.168.0.122:11434/v1/chat/completions` |
| LM Studio (Gaming PC) | `http://192.168.0.89:1234/v1/chat/completions` |
| ❌ Wrong | `http://localhost:2819/...` |

The `localhost` mistake means the API container talks to itself — no LLM lives there.

To fix without rebuilding the image:
```bash
# Stop the running container
docker stop book-translator-api && docker rm book-translator-api
# Restart with correct env
docker run -d --name book-translator-api ... -e BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions ...
```

---

## 6. Frontend files deployed but container serves old version

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

## 7. CWA container crashes or won't start after recreation

- The `cwa-init` service may fail if it can't write to a read-only bind mount.
  Our overlay files are mounted `ro` — this is intentional for the plugin files only.
- If CWA crashes, check: `docker logs calibre-web-automated 2>&1 | tail -50`
- If `cwa-init` complains about file ownership, add `NETWORK_SHARE_MODE=true` env.

---

## 8. Settings gear (⚙) does nothing

- **Fixed in `2026-06-30-ui-polish-v1`.** The menu was being clipped by the control
  bar's `overflow: hidden`; it is now a body-level popover anchored above the pill.
- If the gear still seems dead, you are running an **older cached JS** — hard refresh
  (`Ctrl+Shift+R`) and confirm the console shows `loaded version 2026-06-30-ui-polish-v1`.
- The menu closes on a click outside it or on `Escape`. It shows the UI version, current
  mode/language, a background-prefetch toggle, retry, cache-clear actions, and debug info.

---

## 9. Bilingual translation is glued to the original / hard to read

- **Fixed in `2026-06-30-ui-polish-v1`.** Parent-page CSS does not reach inside the
  EPUB.js `<iframe>`, so the translation styles are now injected directly into the reader
  document. The Spanish line appears under the original with spacing, a blue tint, a
  left border, and a faint background.
- Theme-safe: the blue adapts to white / dark / sepia readers automatically (the plugin
  measures the reader background and sets `data-bt-theme` on the iframe `<html>`).
- If translations look unstyled, the injected `<style id="bt-injected-styles">` may have
  failed — reload the page so the iframe is re-detected.

---

## 10. Some headings / subtitles aren't translated, or are glued

- **Improved in `2026-06-30-ui-polish-v1`.** The selector now covers `h1`–`h6`,
  `blockquote`, and `title`/`subtitle`/`chapter`/`heading`/`epigraph`/`quote` classes,
  and headings render with a dedicated, spaced, centered-when-appropriate style.
- If a specific heading still isn't picked up, it likely uses an unusual class/structure —
  note the element (DevTools → Inspect) so the selector can be extended.
