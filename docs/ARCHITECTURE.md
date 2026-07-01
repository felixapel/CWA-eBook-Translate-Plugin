# Architecture Overview

This document details the architecture of the `book-translator` plugin.

## Overview

The plugin operates as a decoupled overlay integrated into Calibre-Web-Automated (CWA).

```
┌────────────────────────────────┐         HTTP          ┌─────────────────────────┐
│ calibre-web-automated (Reader)  │  ────────────────►  │  book-translator-api    │
│                                │                      │                         │
│  - translator.js (Frontend)     │                      │  - Flask server.py      │
│  - translator.css              │                      │  - translator.py        │
│  - read.html (Injection point) │                      │  - SQLite Cache         │
└────────────────────────────────┘                      └────────────┬────────────┘
                                                                     │ HTTP
                                                                     ▼
                                                        ┌─────────────────────────┐
                                                        │ LLM Provider (vLLM)     │
                                                        └─────────────────────────┘
```

## Component Breakdown

### Frontend (`translator.js`)
- **Lifecycle Observers**: Hooks into CWA reader using iframe document checking and `epub.js` rendition hooks (`relocated`, `rendered`).
- **Translation Management**: Coordinates visible-first translation chunking and background sequential prefetching.
- **Client Cache**: Leverages browser `localStorage` (indexed by a 53-bit cyrb53 hash of the paragraph text) to render translations instantly upon page load or chapter returns.

### Backend (`book-translator-api`)
- **Flask Server (`server.py`)**: Exposes translation endpoints `/translate` and `/translate/batch` along with metrics and health probes.
- **SQLite Cache (`cache.py`)**: Stores translations using SHA-256 hashes of text, source, and target languages to prevent duplicate LLM calls across all clients.
- **LLM Client (`translator.py`)**: Multi-provider wrapper that supports batch translation prompts with dynamic context windows (`BT_CONTEXT_WINDOW`).
