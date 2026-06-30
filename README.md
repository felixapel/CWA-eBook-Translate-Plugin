# CWA Translate Plugin

Bilingual LLM-powered translation overlay for [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). Translate ebooks paragraph-by-paragraph while reading, using local LLMs (vLLM, LM Studio, Ollama) or any major Cloud API (OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter).

## Features

- 🌐 **Bilingual reading** — original + translation side by side
- 🔄 **Three modes** — Bilingual / Translation-only / Original
- ⚡ **Visible-First Translation** — prioritizes paragraphs visible on screen for instant rendering
- 🚀 **Background Prefetching** — translates the rest of the chapter sequentially in the background
- 🌍 **Multi-Language Support** — built-in language selector and UI localized to browser language
- 📚 **Deep DOM Parsing** — accurately captures headings, custom title classes, and clickable TOC links
- 💾 **SHA-256 cache** — never re-translates the same paragraph (SQLite)
- 🔒 **Rate limited** — protects your API keys and GPU from runaway requests

## Architecture

```text
Unraid Server
┌────────────────────────┐             ┌────────────────────────────────┐
│ CWA (:8383)            │   HTTP      │ book-translator-api (:8390)    │
│ ┌────────────────────┐ │ ─────────►  │ ├─ POST /translate             │
│ │ Overlay files:     │ │             │ ├─ POST /translate/batch       │
│ │ translator.js      │ │             │ ├─ GET  /health                │
│ │ translator.css     │ │             │ ├─ GET  /metrics               │
│ └────────────────────┘ │             │ └─ SQLite cache                │
└───────────┬────────────┘             │ └────────────┬─────────────────┤
            │                          └──────────────│─────────────────┘
     NGINX (SWAG)                                     │ HTTP
     Proxy Route: /translate           ┌──────────────▼─────────────────┐
                                       │ Providers:                     │
                                       │ Local, OpenAI, Anthropic,      │
                                       │ Gemini, Groq, Together, MiniMax│
                                       │ DeepSeek, OpenRouter           │
                                       └────────────────────────────────┘
```

## Installation

### 1. Build and Run Backend (Docker)

```bash
cd ~/.hermes/projects/book-translator
docker build -t local/book-translator-api:latest .
```

Run via Docker Compose or Unraid Template mapping port `8390` and the volume `/mnt/user/appdata/book-translator:/app/data`.

### 2. CWA Overlay & NGINX

1. Inject `translator.js` and `translator.css` into Calibre-Web-Automated (using an overlay volume mount or by copying the files directly into the container's static folder).
2. Configure SWAG (or your reverse proxy) to route `/translate` to the `book-translator-api` container:

```nginx
# calibre-web.subdomain.conf
location /translate {
    include /config/nginx/proxy.conf;
    include /config/nginx/resolver.conf;
    set $upstream_app book-translator-api;
    set $upstream_port 8390;
    set $upstream_proto http;
    proxy_pass $upstream_proto://$upstream_app:$upstream_port;
}
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/translate` | Translate single paragraph |
| `POST` | `/translate/batch` | Translate multiple paragraphs |
| `GET` | `/health` | Health check with backend status |
| `GET` | `/stats` | Cache statistics |
| `GET` | `/metrics` | Request metrics and latency |
| `POST` | `/cache/cleanup` | Evict old cache entries |

## Configuration

Environment variables (Docker):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `local` | `local`, `openai`, `anthropic`, `gemini`, `groq`, `together`, `minimax`, `deepseek`, `openrouter` |
| `LLM_MODEL` | `gemma4-12b` | Model name for the chosen provider |
| `LLM_API_KEY` | | Your API key for the chosen provider |
| `BT_LOCAL_URL` | `http://192.168.0.122:2819/v1/chat/completions` | Only used if `LLM_PROVIDER=local` |

## License

MIT
