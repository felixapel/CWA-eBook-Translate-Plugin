# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-06-30
UI version marker: `2026-06-30-ui-polish-v1` (backend/frontend rate limit improvements).

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

## [Unreleased]
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
