# Architecture Overview

This document details the architecture of the `book-translator` plugin.

## Overview

The plugin operates as a decoupled overlay integrated into Calibre-Web-Automated (CWA).

There are two integration methods:

1. **Proxy-injection mode (recommended, 2.0.0+).** The container runs nginx in
   front of a **stock** CWA instance (`CWA_UPSTREAM`). HTML responses get a
   single `<script src="/bt-static/loader.js">` tag injected before `</head>`;
   `loader.js` self-guards to `/read/` pages and loads the overlay. The API is
   reachable same-origin under `/bt-api/`, so CORS never applies. Because only
   one tag is injected (instead of maintaining a forked `read.html`), CWA
   template updates cannot silently break or drop the plugin. See
   `proxy/nginx.conf.template` and `docker-entrypoint.sh`.
2. **Bind-mount mode (advanced/development).** `overlay/read.html` plus the
   JS/CSS are mounted into the CWA container; the overlay calls the API on
   `:8390` cross-origin. The template copy is tracked against the CWA version
   pinned in `docker-compose.yml`.

The diagram below shows the bind-mount data flow; in proxy mode the same
frontend/API components apply, with nginx in front.

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
