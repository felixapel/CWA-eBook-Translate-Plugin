# CWA Translate Plugin

Bilingual LLM-powered translation overlay for [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). Translate ebooks paragraph-by-paragraph while reading, using local LLMs (vLLM, LM Studio, Ollama) or cloud APIs (MiniMax-M3).

## Features

- 🌐 **Bilingual reading** — original + translation side by side
- 🔄 **Three modes** — Bilingual / Translation-only / Off (cycle with `Ctrl+T`)
- ⚡ **Local LLM first** — uses your GPU (vLLM gemma4-12b), falls back to MiniMax-M3
- 💾 **SHA-256 cache** — never re-translates the same paragraph (SQLite)
- 🚀 **Async prefetch** — pre-translates upcoming pages in background
- 🌙 **Dark mode** — works with all 4 CWA themes (light, dark, sepia, black)
- 📊 **Metrics** — request counts, cache hit rate, latency tracking
- 🔒 **Rate limited** — protects your GPU from runaway requests

## Architecture

```
Unraid Docker                          Hermes VM
┌────────────────────────┐             ┌──────────────────────────┐
│ CWA (:8383)            │   HTTP      │ book-translator (:8390)  │
│ ┌────────────────────┐ │ ─────────►  │ ├─ POST /translate       │
│ │ Overlay files:     │ │             │ ├─ POST /translate/batch │
│ │ read.html          │ │             │ ├─ POST /prefetch        │
│ │ translator.js/css  │ │             │ ├─ GET  /health          │
│ └────────────────────┘ │             │ ├─ GET  /metrics         │
└────────────────────────┘             │ └─ SQLite cache          │
                                       └────────────┬─────────────┘
                                                    │ HTTP
                                       ┌────────────▼─────────────┐
                                       │ vLLM / LM Studio / Ollama│
                                       │ gemma4-12b (primary)     │
                                       │ MiniMax-M3 (fallback)    │
                                       └──────────────────────────┘
```

## Installation

### 1. Backend Service

```bash
cd ~/.hermes/projects/book-translator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure backends
export BT_LOCAL_URL="http://your-llm-server:2819/v1/chat/completions"
export BT_LOCAL_MODEL="gemma4-12b"
export BT_LOCAL_ENABLED=1

# Run with gunicorn
gunicorn -w 2 -b 0.0.0.0:8390 --timeout 120 server:app
```

### 2. CWA Docker Overlay

Add these volume mounts to your CWA `docker-compose.yml`:

```yaml
services:
  calibre-web:
    volumes:
      - ./overlay/read.html:/app/calibre-web-automated/cps/templates/read.html
      - ./static/translator.js:/app/calibre-web-automated/cps/static/js/translator.js
      - ./static/translator.css:/app/calibre-web-automated/cps/static/css/translator.css
```

Then restart: `docker compose restart calibre-web`

### 3. Alternative: Userscript (Tampermonkey)

Install `userscript/cwa-translator.user.js` in Tampermonkey/Greasemonkey — no Docker changes needed.

## Systemd Service

```bash
cp book-translator.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now book-translator
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/translate` | Translate single paragraph |
| `POST` | `/translate/batch` | Translate multiple paragraphs |
| `POST` | `/prefetch` | Async pre-translate (returns immediately) |
| `GET` | `/prefetch/<id>/status` | Check prefetch job status |
| `GET` | `/health` | Health check with backend status |
| `GET` | `/stats` | Cache statistics |
| `GET` | `/metrics` | Request metrics and latency |
| `POST` | `/cache/cleanup` | Evict old cache entries |

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BT_LOCAL_URL` | `http://192.168.0.122:2819/v1/chat/completions` | Local LLM endpoint |
| `BT_LOCAL_MODEL` | `gemma4-12b` | Model name for local backend |
| `BT_LOCAL_ENABLED` | `1` | Enable/disable local backend |

MiniMax API key is loaded from `~/.hermes/auth.json` (credential_pool.minimax) or `~/.hermes/.env` (MINIMAX_API_KEY=...).

## License

MIT
