# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
