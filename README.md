# CWA Translate Plugin

Bilingual LLM-powered translation overlay for [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). Translate ebooks paragraph-by-paragraph while reading, using local LLMs (vLLM, LM Studio, Ollama) or any major Cloud API (OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter).

## ✨ Features

- 🌐 **Bilingual reading** — original + translation side by side
- 🔄 **Three modes** — Bilingual / Translation-only / Original
- ⚡ **Visible-First Translation** — prioritizes paragraphs visible on screen for instant rendering
- 🚀 **Background Prefetching** — translates the rest of the chapter sequentially in the background
- 🌍 **Multi-Language Support** — built-in language selector and UI localized to browser language
- 🧠 **Context-Aware Translation** — feeds previous/next paragraphs to the LLM to improve literary quality and character voice
- 📚 **Deep DOM Parsing** — accurately captures headings, custom title classes, and clickable TOC links
- 💾 **Persistent Double Cache** — server-side SQLite (SHA-256) + client-side `localStorage` caching ensures you never lose a translation or re-pay API costs
- 🔒 **Rate limited & Stable** — protects your API keys and GPU from runaway requests, featuring `AbortController` cancellation for perfectly responsive UI buttons

---

## ⚡ Performance

Throughput and latency depend entirely on your LLM backend (local GPU/model vs. a
cloud API) and on the tunables in [Configuration](#️-configuration) — there is no
single number that applies to every setup, so we don't publish one. Two scripts are
included so you can measure *your* deployment:

- `benchmark.py` — quick concurrent load test against a running API.
- `benchmark_realistic.py` — simulates a realistic reading session (visible-page +
  background prefetch) against a live backend.

Run either with the API up (`python benchmark.py` / `python benchmark_realistic.py`)
and read the printed p50/p95/throughput for your own hardware.

If cold translations feel slow, see `BT_BATCH_SIZE`, `BT_OUTPUT_TOKEN_FACTOR`, and
`BT_MAX_CONCURRENT` below, and `docs/TROUBLESHOOTING.md`.

---

## 🚀 Installation

> Replace `<your-repo-url>` below with wherever you've cloned/forked this project
> (GitHub, your own Gitea, etc). This project isn't published as a container image
> or a hosted installer script — you run it from a local clone.

### Option 1: Docker Compose (recommended)

`docker-compose.yml` spins up Calibre-Web-Automated together with the Translator
API, pre-wired to inject the plugin files via bind mounts:

```bash
git clone <your-repo-url> CWA-translate-plugin
cd CWA-translate-plugin
docker-compose up -d
```

Edit the `book-translator-api` service's environment block first (at least
`BT_LOCAL_URL` if you're using a local LLM) — see [Configuration](#️-configuration).

### Option 2: Unraid (community-applications style)

`install_unraid.sh` copies the overlay files into your CWA appdata folder and
installs an Unraid Docker template for the API. Review the script, then run it
**locally** (don't pipe an unreviewed remote script into `bash`):

```bash
git clone <your-repo-url> CWA-translate-plugin
cd CWA-translate-plugin
chmod +x install_unraid.sh
./install_unraid.sh
```

**Post-Install Steps**:
1. Go to your Unraid Docker tab and edit your `calibre-web-automated` container.
2. Add the 3 paths (as instructed by the script) to inject the plugin files.
3. Deploy the newly added `book-translator-api` container.

`deploy_unraid.sh` / `verify_unraid.sh` are personal SSH-based redeploy/verify
helpers for an existing install — read them and adapt the host/paths before use.

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
| `BT_LOCAL_URL` | `http://localhost:1234/v1/chat/completions` | Only used if `LLM_PROVIDER=local`. OpenAI-compatible endpoint — the **path is always `/v1/chat/completions`** (vLLM, LM Studio, Ollama, llama.cpp all speak it); only host:port changes (vLLM `:8000`, LM Studio `:1234`, Ollama `:11434`). **In Docker, `localhost` is the container itself** — use `http://host.docker.internal:<port>/...` or the host IP. |
| `BT_MAX_CONCURRENT` | `2` | Simultaneous translation requests (batches). For a slow single-GPU local model, `1`–`2` is **more** stable than `3` (avoids timeout cascades). |
| `BT_BATCH_SIZE` | `5` | Paragraphs translated per LLM call. `>1` is dramatically faster on slow models (one generation instead of one-per-paragraph); if the model's segmented reply can't be parsed it transparently falls back to per-paragraph. Set `1` for legacy one-call-per-paragraph. |
| `BT_MAX_TOKENS` | `4096` | Hard ceiling on `max_tokens` for a **single**-paragraph request. The actual value sent is the smaller of this and the proportional cap (see `BT_OUTPUT_TOKEN_FACTOR`). |
| `BT_BATCH_MAX_TOKENS` | `8192` | Same ceiling, but for a **batched** (multi-paragraph) request. |
| `BT_OUTPUT_TOKEN_FACTOR` | `2.0` | Caps generated `max_tokens` at `input_tokens × FACTOR + FLOOR`, clamped to the ceiling above. Prevents a rambling/stuck local model from generating thousands of tokens for a short paragraph (the main cause of 8–20s and 120s stalls). `2.0` never truncates real translations; lower it (e.g. `1.6`) for a bit more speed at some risk on very expansive target languages. |
| `BT_OUTPUT_TOKEN_FLOOR` | `256` | Minimum `max_tokens` per request. |
| `BT_CONTEXT_WINDOW` | `0` | Number of previous/next paragraphs to include as context for the LLM during translation. Set to `1` or `2` for context-aware translations. Context improves literary quality but consumes more tokens per request. |
| `BT_TIMEOUT` | `60` | Seconds before a single translation request is abandoned. Raise it if a slow local model times out on long paragraphs; lower it (with a smaller `BT_BATCH_SIZE`) if you'd rather fail fast under contention. |
| `LLM_FALLBACK_PROVIDER` | | Optional. A secondary provider used automatically when the primary fails (e.g. `minimax` while `local` is slow/down). |
| `LLM_FALLBACK_MODEL` | | Model name for the fallback provider. |
| `LLM_FALLBACK_API_KEY` | | API key for the fallback provider. |
| `BT_API_TOKEN` | | Optional shared secret. When set, translate endpoints require the `X-BT-Token` header — use it if the API is reachable beyond your LAN. Set the matching `apiToken` in `window.BOOK_TRANSLATOR` (see `read.html`). |
| `BT_RATE_LIMIT_PER_MINUTE` | `120` | Max requests per client IP per 60s window before the API returns `429`. |
| `BT_RATE_LIMIT_RETRY_AFTER` | `10` | Seconds reported in the `Retry-After` header / response body on a `429`. The frontend reads this and backs off automatically. |
| `DB_PATH` | `translations.db` | Path to the SQLite translation cache. In Docker this should point inside the `/app/data` volume (the provided Dockerfile/compose already set it to `/app/data/translations.db`) so the cache survives container recreation. |
| `PORT` | `8390` | Port the API listens on. If you remap it, also update the `-p`/compose port mapping and any reverse-proxy route — `EXPOSE 8390` in the Dockerfile is documentation only. |
| `MINIMAX_API_KEY` | | Legacy fallback for `LLM_API_KEY` (only read when `LLM_API_KEY` is unset). Prefer `LLM_API_KEY`. |

> **Why a single gunicorn worker?** Rate limiting, request metrics, and the health
> cache are kept in process memory for simplicity. Running more than one worker
> would give each its own copy (e.g. the rate limit becoming `N×` the configured
> value). The `--threads 8` setting already gives plenty of request concurrency
> within that one worker — don't raise `--workers` without moving that state to
> something shared (e.g. SQLite, like the translation cache already is).

---

## 🏗️ Architecture

```text
Unraid Server / Docker Host
┌────────────────────────┐             ┌────────────────────────────────┐
│ CWA (:8383)            │   HTTP      │ book-translator-api (:8390)    │
│ ┌────────────────────┐ │ ─────────►  │ ├─ POST /translate             │
│ │ Overlay files:     │ │             │ ├─ POST /translate/batch       │
│ │ translator.js      │ │             │ ├─ GET  /ping (liveness)       │
│ │ translator.css     │ │             │ ├─ GET  /health (deep probe)   │
│ │ read.html          │ │             │ ├─ GET  /metrics, /stats       │
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
