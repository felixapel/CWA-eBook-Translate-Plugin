# CWA Translate Plugin

Bilingual LLM-powered translation overlay for [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). Translate ebooks paragraph-by-paragraph while reading, using local LLMs (vLLM, LM Studio, Ollama) or any major Cloud API (OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter).

## ✨ Features

- 🌐 **Bilingual reading** — original + translation side by side
- 🔄 **Three modes** — Bilingual / Translation-only / Original
- ⚡ **Visible-First Translation** — prioritizes paragraphs visible on screen for instant rendering
- 🚀 **Background Prefetching** — translates the rest of the chapter sequentially in the background
- 🌍 **Multi-Language Support** — built-in language selector and UI localized to browser language
- 📚 **Deep DOM Parsing** — accurately captures headings, custom title classes, and clickable TOC links
- 💾 **SHA-256 cache** — never re-translates the same paragraph (SQLite)
- 🔒 **Rate limited** — protects your API keys and GPU from runaway requests

---

## 🚀 Installation

### Option 1: Easy Installation for Unraid (Recommended)

We have created an automated installer script for Unraid users. Open your Unraid Terminal and run:

```bash
curl -sL https://raw.githubusercontent.com/username/CWA-translate-plugin/main/install_unraid.sh | bash
```

The script will automatically:
1. Download the plugin frontend files (`translator.js`, `translator.css`, `read.html`) to your `appdata/calibre-web-automated` folder.
2. Install the `book-translator-api` Docker template into your Unraid GUI.

**Post-Install Steps**:
1. Go to your Unraid Docker tab and edit your `calibre-web-automated` container.
2. Add the 3 paths (as instructed by the script) to inject the plugin files.
3. Deploy the newly added `book-translator-api` container!

### Option 2: Easy Installation (Docker Compose)

For standard Docker users, we provide a full `docker-compose.yml` that spins up Calibre-Web-Automated along with the Translator API, already pre-configured to inject the plugin files.

```bash
git clone https://github.com/username/CWA-translate-plugin.git
cd CWA-translate-plugin
docker-compose up -d
```

### Option 3: Manual Installation

1. Build and run the `book-translator-api` backend container manually.
2. Inject `translator.js`, `translator.css` and `read.html` into Calibre-Web-Automated (using an overlay volume mount or copying files directly).
3. Configure your reverse proxy (SWAG, Traefik, NPM) to route `/translate` to the API container. Example for NGINX/SWAG:
   ```nginx
   location /translate {
       include /config/nginx/proxy.conf;
       include /config/nginx/resolver.conf;
       set $upstream_app book-translator-api;
       set $upstream_port 8390;
       set $upstream_proto http;
       proxy_pass $upstream_proto://$upstream_app:$upstream_port;
   }
   ```

---

## ⚙️ Configuration

Environment variables for the `book-translator-api` container:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `local` | `local`, `openai`, `anthropic`, `gemini`, `groq`, `together`, `minimax`, `deepseek`, `openrouter` |
| `LLM_MODEL` | `gemma4-12b` | Model name for the chosen provider |
| `LLM_API_KEY` | | Your API key for the chosen provider |
| `BT_LOCAL_URL` | `http://localhost:1234/v1/chat/completions` | Only used if `LLM_PROVIDER=local`. **In Docker, `localhost` is the container itself** — point this at your host (e.g. `http://host.docker.internal:1234/...` or the host IP). |
| `BT_MAX_CONCURRENT` | `2` | Simultaneous translation requests per batch. For a slow single-GPU local model, `1`–`2` is **more** stable than `3` (avoids timeout cascades). |
| `BT_TIMEOUT` | `60` | Seconds before a single translation request is abandoned. Raise it if a slow local model times out on long paragraphs. |
| `LLM_FALLBACK_PROVIDER` | | Optional. A secondary provider used automatically when the primary fails (e.g. `minimax` while `local` is slow/down). |
| `LLM_FALLBACK_MODEL` | | Model name for the fallback provider. |
| `LLM_FALLBACK_API_KEY` | | API key for the fallback provider. |

---

## 🏗️ Architecture

```text
Unraid Server / Docker Host
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

## 📜 License

MIT
