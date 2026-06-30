# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- Multi-provider support directly from environment variables.
- Integration for OpenRouter and DeepSeek via standard `requests`.
- `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY` environment variables.
- Standardized GitHub/Gitea templates and community health files.

### Changed
- Refactored `translator.py` architecture to use `requests` over `urllib`.
- Swapped background `_translate_paragraphs` processing to use concurrent `ThreadPoolExecutor` fetching.
- Upgraded default Gunicorn execution strategy to `1 worker / 8 threads` to prevent memory drift across application states.
- Enhanced `triggerPrefetch` inside `translator.js` to intelligently memoize inner text prior to payload generation.

### Fixed
- Cache DB collisions (Database is Locked) resolved by enabling `PRAGMA busy_timeout=5000` and minimizing read locking.
- Resolved memory leakage in Rate Limiter dictionary by implementing an hourly background cleaner.
- Mitigated negative JS hash generation limits by enforcing strict unsigned zero-shifted bits.

## [1.0.0] - 2026-06-25
### Added
- Initial bilingual translation overlay release.
- SQLite SHA-256 fallback cache system.
- Light/Dark mode integration with CWA internal iframe rendering.
- `translator.js` client logic for dynamic DOM injection.
