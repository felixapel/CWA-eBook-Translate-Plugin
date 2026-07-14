# CWA eBook Translate Plugin

Bilingual LLM-powered translation overlay for [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). Translate ebooks paragraph-by-paragraph while reading — in **100+ languages** — using local LLMs (vLLM, LM Studio, Ollama) or any major Cloud API (OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter).

![Bilingual reading demo](docs/assets/demo.gif)

## ✨ Features

- 🌐 **Bilingual reading** — original + translation side by side
- 🔄 **Three modes** — Bilingual / Translation-only / Original
- 🌍 **100+ source and target languages** — choose the book language in Settings and the translation language in the toolbar. The pickers show the 10 most-spoken languages first, then every other supported language A–Z (type to jump). Developed and tuned against **Google's Gemma 4** as the default local model; the language set mirrors Gemma's pre-training coverage
- ⚡ **Visible-First Translation** — prioritizes paragraphs visible on screen for instant rendering
- 🚀 **Background Prefetching** — translates the rest of the chapter sequentially in the background
- 🧠 **Context-Aware Translation** — feeds surrounding paragraphs to the LLM to improve literary quality and character voice
- 📚 **Deep DOM Parsing** — accurately captures headings, custom title classes, and clickable TOC links
- 💾 **Private Bounded Cache** — durable server-side SQLite uses scoped SHA-256 keys, mandatory TTL/cap, and private file modes; browser persistence is opt-in on trusted single-user devices
- 🔒 **Rate limited & Stable** — request-size caps, per-client authentication admission, and per-subject API quotas protect your API keys and GPU from runaway requests, with `AbortController` cancellation for responsive UI buttons
- 🔌 **Zero-touch install** — proxy-injection mode overlays a **stock** CWA container: no template mounts, nothing to re-apply when CWA updates

### A note on language quality

The default model, **Gemma 4** (`gemma4-12b`), is pre-trained on 140+ languages
with ~35 languages receiving first-class, benchmarked support (all major
European, East Asian, South/Southeast Asian, and Middle Eastern languages).
The remaining languages in the picker come from Gemma's wider pre-training
corpus: translation works, but lower-resource languages (e.g. Nahuatl, Chewa,
Tibetan) can occasionally lose coherence or bleed into a dominant language on
complex passages. Cloud models (GPT, Claude, Gemini) generally handle the
lower-resource tier better — switch `LLM_PROVIDER` if a language matters to you.

---

## 🚀 Installation

The supported install is managed by `btctl`. It builds this exact clean source
checkout into one immutable local image and creates two isolated roles: a
browser-facing injection proxy and an unpublished translation API. CWA itself
is never modified or owned by the installer.

Start from either an annotated release tag or a full reviewed commit. If a
version tag has not been published yet, use the exact candidate commit supplied
by the maintainer; do not invent the tag or substitute a mutable `latest` image.

```bash
git clone <repository-url> cwa-translate
cd cwa-translate
git fetch --tags
git switch --detach <release-tag-or-full-reviewed-commit>
```

On stock Unraid, the same public `./btctl` command starts a temporary local
operator through Docker and does not require host Python or NerdTools. It does
require Bash, a working Docker daemon, root, and a full Git checkout including
its `.git` directory. If the Unraid host has no Git client, Claude Code or any
other Git-capable machine can prepare the exact checkout and copy that complete
directory to Unraid; extracting only a release ZIP or tarball is not sufficient
for the source-identity checks. Obtain the public launcher from that same
trusted commit: because it runs as root, the launcher and its embedded pinned
source-exporter definition are the bootstrap trust root.

Copy [`.env.example`](.env.example) outside the checkout, make it private, and
edit the deployment-specific values. For a local OpenAI-compatible LLM,
`LLM_API_KEY` stays empty: this open-source project has no project key, registry
credential, signing secret, or browser token.

```bash
install -d -m 0700 /absolute/private/path
cp .env.example /absolute/private/path/cwa-translate.env
chmod 0600 /absolute/private/path/cwa-translate.env
```

Choose `BT_INSTALL_PROFILE=unraid` or `compose-existing`, then set the exact CWA
container, its matching `http://<container>:8083` upstream, Docker network, CWA
version, CWA reverse-proxy identity header, public reader origin, storage paths,
and LLM endpoint. Validate first, install second, and prove the running state:

```bash
./btctl plan --env /absolute/private/path/cwa-translate.env
./btctl install --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

`plan` does not change deployment files, state, CWA, or running containers. On
a host without Python, its automatic bootstrap builds and removes temporary
helper images, forces the local `/var/run/docker.sock`, and may warm the Docker
build cache. It reports the exact
version+commit image, roles, ports, paths, CWA evidence, and ownership while
redacting API keys. `install` writes state only after live postconditions pass.
`doctor` is read-only and must finish with every check marked `ok`.

```text
Browser/reverse proxy -> cwa-translate-proxy -> stock CWA
                                  |
                                  +-> cwa-translate-api -> LLM
                                               |
                                               +-> private cache
```

In normal `published` mode, only the injection proxy gets a host port; the API
does not. Read CWA through that proxy port or point the existing domain's main
route at it. Keep OPDS/Kobo routes pointed directly at CWA. The default
`cwa-session` profile validates the existing HttpOnly CWA session and places no
translator credential in JavaScript or `localStorage`.

Use the guide for your host:

- [Unraid deployment](docs/DEPLOY_UNRAID.md) — root-owned appdata, DockerMan
  templates, v2.1.4 upgrade, rollback, and acceptance checks.
- [Existing Compose deployment](docs/DEPLOY_COMPOSE.md) — the same managed
  split topology without taking ownership of the CWA project.
- [Compatibility matrix](docs/COMPATIBILITY.md) — tested CWA, platform,
  browser, reverse-proxy, and LLM boundaries.
- [Authentik forwarded identity](docs/AUTHENTIK.md) — advanced fail-closed edge
  topology for installations where Authentik, rather than a native CWA session,
  is the identity authority.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — start with `btctl doctor` and
  follow symptom-specific checks.

### Lifecycle commands

Use the same private environment file for every operation:

```bash
./btctl doctor --env /absolute/private/path/cwa-translate.env
./btctl adopt --env /absolute/private/path/cwa-translate.env
./btctl upgrade --env /absolute/private/path/cwa-translate.env --yes
./btctl rollback --env /absolute/private/path/cwa-translate.env --yes
./btctl uninstall --env /absolute/private/path/cwa-translate.env --yes
```

`adopt` only reconstructs lost state from an exact, already-labeled split
runtime. Fresh `install` and `adopt` require CWA 4.x. `upgrade` is exclusively
for the supported CWA 3.1.4/v2.1.4 migration and keeps the old container
restartable. `rollback` restores that exact legacy runtime.
`uninstall` removes only owned translator runtime resources and preserves CWA,
translation data, state evidence, and backups. See
[ADR-010](docs/decisions/ADR-010-btctl-state-and-ownership.md) for the ownership
model.

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

## ⚙️ Configuration

Most operators should edit only the high-level values in [`.env.example`](.env.example).
`btctl` derives the internal authentication, role, network, and browser values;
do not copy low-level compatibility settings into a managed install. The table
below documents the complete translator image interface for development and
legacy integrations.

| Variable | Default | Description |
|----------|---------|-------------|
| `BT_ROLE` | `auto` | Runtime role: `api`, `proxy`, or compatibility-only `all`. `auto` selects `api` without `CWA_UPSTREAM` and `all` when it is present. The reference Compose file sets roles explicitly. |
| `CWA_UPSTREAM` | | Required by the proxy role. Exact base URL of the stock CWA instance (e.g. `http://calibre-web:8083`); credentials, paths, queries, fragments, and non-HTTP schemes fail startup. Managed installs require exactly `http://<BT_CWA_CONTAINER>:8083`, binding session validation and proxy traffic to the inspected container identity. |
| `BT_API_UPSTREAM` | `http://127.0.0.1:$PORT` | Exact translation API base URL used by the proxy role. The managed split topology uses the network-scoped `http://<install-name>-api:8390` container identity on its private Docker network; the same URL validation applies. |
| `BT_PROXY_PORT` | `8080` | Container port for the injection proxy (proxy mode only). |
| `BT_PUBLIC_ORIGIN` | | **Required by proxy/all roles and by the reference Compose file.** Exact browser-facing origin, such as `http://192.168.1.10:8084` or `https://books.example.com`. Its validated host and scheme are the only values forwarded to CWA/API; there is no implicit `localhost` deployment value. |
| `BT_CWA_MAX_BODY_SIZE` | `2g` | Finite nginx cap for CWA uploads. Use a positive nginx size such as `512m` or `4g`; `0`/unlimited and directive-like values fail startup. Translation API bodies retain a separate 3 MiB proxy cap and the stricter Flask limit. |
| `BT_CWA_IDENTITY_HEADER` | `Remote-User` | Header CWA is configured to trust for reverse-proxy login. The bundled injection proxy always strips this client-supplied credential before forwarding to CWA because it is not an identity authority. If CWA uses a custom header name, set the same exact name here; leaving them mismatched can permit header-forgery login through a directly exposed proxy. Use a separate identity-aware proxy and the documented `forwarded` API mode when header-based SSO is required. |
| `BT_AUTH_MODE` | `token` | Authentication authority: `cwa_session` (recommended proxy topology), `forwarded` (identity-aware reverse proxy), `token` (shared-secret compatibility), or development-only `disabled`. The default fails startup unless `BT_API_TOKEN` is present. Disabled mode additionally requires `BT_ALLOW_INSECURE_AUTH=true`. `/ping`, `/health`, and `/ready` stay unauthenticated; every other route is protected. |
| `BT_ALLOW_INSECURE_AUTH` | `false` | Required second acknowledgement for `BT_AUTH_MODE=disabled`. Never enable it in production. |
| `BT_CWA_AUTH_URL` | | Required for `cwa_session`, e.g. `http://calibre-web:8083/ajax/emailstat`. Only that exact path is accepted. The API forwards selected cookies, refuses redirects, and requires CWA's bounded JSON task-list response; it returns `503` when the authority cannot be evaluated. |
| `BT_CWA_AUTH_COOKIE_NAMES` | `session,remember_token` | CWA cookie names allowed to leave the API for the configured auth probe. All other browser cookies are dropped. |
| `BT_CWA_AUTH_TIMEOUT_SECONDS` | `2` | Bounded CWA session-probe timeout. |
| `BT_CWA_AUTH_CACHE_TTL_SECONDS` | `15` | Short positive/negative validation-cache TTL. Keys are one-way session hashes; raw cookies are never cached or logged. |
| `BT_CWA_AUTH_CACHE_MAX_ENTRIES` | `10000` | Maximum cached session-validation decisions. Oldest entries are evicted. |
| `BT_CWA_AUTH_MAX_INFLIGHT` | `8` | Maximum distinct CWA probes active at once; concurrent checks of the same session are coalesced. Saturation fails closed with `503`. |
| `BT_CWA_AUTH_MAX_RESPONSE_BYTES` | `262144` | Maximum decompressed bytes read from the CWA auth probe before JSON parsing. Oversized responses fail closed with `503`. |
| `BT_IDENTITY_TRUSTED_PROXIES` | | Required for `forwarded`. Comma-separated CIDRs/IPs allowed to set `X-BT-Subject` and optional `X-BT-Roles`; direct client headers are rejected. The subject is hashed before use as a tenant. The identity proxy must strip client-supplied copies before setting its own and be the API's immediate peer. The bundled injection proxy deliberately strips these headers and is not an identity authority; route `/bt-api` directly through the allowlisted identity proxy with no public bypass. |
| `BT_AUTH_RATE_LIMIT_PER_MINUTE` | `300` | Separate per-client limit for protected-route authentication attempts, including rejected credentials and observability endpoints. |
| `BT_RATE_LIMIT_MAX_CLIENTS` | `10000` | Maximum active client buckets in each in-memory limiter. Under saturated active cardinality, unseen clients fail closed with `429` instead of growing process memory or receiving a fresh allowance. |
| `LLM_PROVIDER` | `local` | `local`, `openai`, `anthropic`, `gemini`, `groq`, `together`, `minimax`, `deepseek`, `openrouter` |
| `LLM_MODEL` | `gemma4-12b` | Model name for the chosen provider |
| `LLM_API_KEY` | | Your API key for the chosen provider (the only supported key mechanism since 2.0.0) |
| `BT_LOCAL_URL` | `http://localhost:1234/v1/chat/completions` | Only used if `LLM_PROVIDER=local`. OpenAI-compatible endpoint — the **path is always `/v1/chat/completions`** (vLLM, LM Studio, Ollama, llama.cpp all speak it); only host:port changes (vLLM `:8000`, LM Studio `:1234`, Ollama `:11434`). **In Docker, `localhost` is the container itself** — use `http://host.docker.internal:<port>/...` or the host IP. |
| `BT_MAX_CONCURRENT` | `2` | Simultaneous translation requests (batches). For a slow single-GPU local model, `1`–`2` is **more** stable than `3` (avoids timeout cascades). |
| `BT_BATCH_SIZE` | `5` | Paragraphs translated per LLM call. `>1` is dramatically faster on slow models (one generation instead of one-per-paragraph). Batches use a strict, versioned JSON envelope; an invalid provider response fails the group atomically and is never cached. Set `1` for one-call-per-paragraph. |
| `BT_MAX_TOKENS` | `4096` | Hard ceiling on `max_tokens` for a **single**-paragraph request. The actual value sent is the smaller of this and the proportional cap (see `BT_OUTPUT_TOKEN_FACTOR`). |
| `BT_BATCH_MAX_TOKENS` | `8192` | Same ceiling, but for a **batched** (multi-paragraph) request. |
| `BT_OUTPUT_TOKEN_FACTOR` | `2.0` | Caps generated `max_tokens` at `input_tokens × FACTOR + FLOOR`, clamped to the ceiling above. Prevents a rambling/stuck local model from generating thousands of tokens for a short paragraph (the main cause of 8–20s and 120s stalls). `2.0` never truncates real translations; lower it (e.g. `1.6`) for a bit more speed at some risk on very expansive target languages. |
| `BT_OUTPUT_TOKEN_FLOOR` | `256` | Minimum `max_tokens` per request. |
| `BT_CONTEXT_WINDOW` | `0` | Number of surrounding paragraphs included as a do-not-translate `[CONTEXT]` block in batch prompts. Set to `1` or `2` for context-aware translations. Improves literary quality but consumes more tokens per request. |
| `BT_TIMEOUT` | `60` | Seconds before a single translation request is abandoned. Raise it if a slow local model times out on long paragraphs; lower it (with a smaller `BT_BATCH_SIZE`) if you'd rather fail fast under contention. |
| `LLM_FALLBACK_PROVIDER` | | Optional secondary provider. A `local` fallback may run automatically; every remote/cloud fallback is excluded from network calls, cache lookup, and singleflight unless the current API request includes `"allow_cloud_fallback": true`. |
| `LLM_FALLBACK_MODEL` | | Model name for the fallback provider. |
| `LLM_FALLBACK_API_KEY` | | API key for the fallback provider. |
| `BT_API_TOKEN` | | Required when `BT_AUTH_MODE=token`; send it as `X-BT-Token`. This compatibility mode gives every caller one shared tenant and the secret is JavaScript-readable if placed in `window.BOOK_TRANSLATOR`, so prefer `cwa_session` or `forwarded`. It is also the operator credential for `/cache/cleanup` and `/health/deep`; without it those two routes use the private persisted cleanup token in `/app/data`. The proxy loader never reads a token from `localStorage`. |
| `BT_MAX_BATCH_PARAGRAPHS` | `50` | Max paragraphs accepted per `/translate/batch` request (oversized requests get `413`). Protects your GPU/API bill from a single runaway request. |
| `BT_MAX_PARAGRAPH_CHARS` | `8000` | Max characters per paragraph (`413` beyond it). |
| `BT_MAX_CONTENT_LENGTH` | `2097152` (2 MB) | Hard cap on the request body (the WSGI-level backstop). Per-field caps (`BT_MAX_BATCH_PARAGRAPHS`, `BT_MAX_PARAGRAPH_CHARS`) check the parsed content; this cap rejects oversize bodies before parsing. Lower it for untrusted networks, raise it for very long paragraphs. |
| `BT_MAX_UPSTREAM_INFLIGHT` | `2` | Process-wide cap on simultaneous in-flight LLM calls across all readers. `BT_MAX_CONCURRENT` only bounds one batch request; this cap prevents multi-reader timeout cascades. Must be greater than zero. |
| `BT_UPSTREAM_QUEUE_TIMEOUT` | `2` | Maximum seconds to wait for a global upstream slot. A full queue returns `503` with `Retry-After` without starting a provider call. |
| `BT_SINGLEFLIGHT_MAX_ENTRIES` | `1024` | Process-wide bound on distinct active translation operations. Concurrent requests with the same tenant/book/chapter and exact prompt contract share one provider call; completed results are never retained here and must pass through the scoped SQLite cache. |
| `BT_REQUEST_MAX_ATTEMPTS` | `20` | Maximum provider calls across groups, primary, and an allowed fallback for one API request. With explicit cloud consent, batch groups use one attempt per provider, so the default exactly covers 50 paragraphs at batch size 5 when the primary fails and a healthy fallback succeeds. The single-text endpoint retains two attempts per provider. Attempts are reserved atomically before network I/O. |
| `BT_REQUEST_MAX_INPUT_BYTES` | `5000000` | Maximum cumulative UTF-8 prompt bytes reserved across every provider attempt in one API request. The default covers two passes over the largest valid default batch, including four-byte Unicode and protocol overhead. |
| `BT_REQUEST_MAX_OUTPUT_TOKENS` | `163840` | Maximum cumulative `max_tokens` reserved across every provider attempt in one API request, sized for the same bounded 20-call batch path. |
| `BT_REQUEST_DEADLINE_SECONDS` | `90` | Absolute request-wide deadline. Once expired, no new provider attempt can start; individual provider timeouts are clamped to the remaining time. |
| `BT_MAX_UPSTREAM_RESPONSE_BYTES` | `1048576` (1 MiB) | Maximum decompressed JSON bytes accepted from one provider call. Responses are streamed and aborted at the boundary before JSON materialization; providers that ignore `max_tokens` cannot grow memory/cache without limit. |
| `BT_RATE_LIMIT_PER_MINUTE` | `120` | Max successful API requests per opaque authenticated subject per 60s window before the API returns `429`. Authentication attempts have the separate client-keyed limit above. |
| `BT_RATE_LIMIT_RETRY_AFTER` | `10` | Seconds reported in the `Retry-After` header / response body on a `429`. The frontend reads this and backs off automatically. |
| `BT_TRUST_PROXY` | `false` | **Legacy/dev only.** When `true`, the API uses the **last** `X-Forwarded-For` hop from any peer for pre-auth client admission. A direct client can spoof it, so do not rely on this in production — prefer `BT_TRUSTED_PROXIES` below. It never changes the post-auth subject quota. |
| `BT_TRUSTED_PROXIES` | (empty) | **Production-safe** observed-client source for low-level deployments. Comma-separated CIDRs/IPs of exact peers allowed to set `X-Forwarded-For`; the last hop keys pre-auth attempt/inflight admission. Managed installs never trust a Docker subnet implicitly. Successful API work is always keyed by the opaque authenticated subject. |
| `BT_ALLOWED_ORIGINS` | `http://localhost:8083,http://localhost:8383` | Comma-separated exact origins allowed for CORS (bind-mount installs; irrelevant in proxy mode, which is same-origin). Add your public reader URL here, e.g. `https://books.example.com`. |
| `BT_ALLOW_PRIVATE_LAN` | `true` | Additionally allow localhost/RFC1918 origins (`10.*`, `192.168.*`, `172.16-31.*`) on any port for non-cookie modes. `cwa_session` always ignores this broad grant: credentialed cross-origin requests require an exact `BT_ALLOWED_ORIGINS` entry and receive `Access-Control-Allow-Credentials: true`. Same-origin proxy mode needs neither. |
| `BT_CACHE_TTL_DAYS` | `90` | Mandatory maximum age for cached translations. Expired rows are never served and are removed during normal writes/stats/cleanup. Must be greater than zero. |
| `BT_CACHE_MAX_ENTRIES` | `100000` | Mandatory hard cap on schema-v2 rows. The least-recently-accessed rows are evicted in the same transaction as a write. Must be greater than zero. |
| `BT_CACHE_HIT_FLUSH_THRESHOLD` | `100` | Number of cache-hit counters batched before SQLite is updated. Translation hits stay read-only between flushes, reducing WAL contention. |
| `BT_CACHE_HARDEN_EXISTING_DIR` | `false` (`true` in image) | Change an existing cache directory to mode `0700`. New directories and all DB/WAL/SHM files are always created private; the container enables this fail-closed check. |
| `DB_PATH` | `translations.db` | Path to the SQLite translation cache. In Docker this should point inside the `/app/data` volume (the provided Dockerfile/compose already set it to `/app/data/translations.db`) so the cache survives container recreation. |
| `PORT` | `8390` | Port the API listens on. If you remap it, also update the `-p`/compose port mapping and any reverse-proxy route — `EXPOSE` in the Dockerfile is documentation only. |

Remote fallback is an additive, fail-closed API contract. Both `POST /translate`
and `POST /translate/batch` accept the optional JSON boolean
`allow_cloud_fallback`; omission means `false`, and strings/numbers are
rejected. The reader exposes an explicit privacy switch explaining that book
text will leave the local deployment. That decision is kept only in the
current book tab and is never restored from `localStorage`. Configuring a cloud
provider as the **primary** provider is a separate operator decision and sends
all normal translation requests to that provider.

> **Why a single gunicorn worker?** Rate limiting, request metrics, and the health
> cache are kept in process memory for simplicity. Running more than one worker
> would give each its own copy (e.g. the rate limit becoming `N×` the configured
> value). The `--threads 8` setting already gives plenty of request concurrency
> within that one worker — don't raise `--workers` without moving that state to
> something shared (e.g. SQLite, like the translation cache already is).

Cache schema v2 intentionally leaves the existing v1 `translations` table
intact for rollback but never serves those unscoped rows. V2 writes only to
`translations_v2`, so the cache re-warms without a destructive migration. V2
stores no source paragraph and hashes tenant/book/chapter identifiers before
persistence. Browser translations stay
in memory unless `window.BOOK_TRANSLATOR.persistCache = true` is explicitly set;
opt-in keys also include stable DOM position to separate repeated text in
different contexts. Legacy `bt_cache_v2_*` localStorage entries are removed on
upgrade.

The bundled injection proxy replaces any inbound forwarding chain with the
immediate peer address it actually observed. This client identity controls only
the bounded authentication-attempt and in-flight admission layer. After a
request authenticates, API work is keyed by the server-owned opaque session,
token, or forwarded subject, so two authenticated users do not consume each
other's API quota. When SWAG/Traefik/NPM is the immediate peer, configure only
that reviewed peer if client-aware pre-auth admission is required; the API never
guesses trust from a supplied forwarding chain.

---

## 🏗️ Architecture

```text
Browser ──► proxy role (:8080) ──► CWA (:8083, stock)
                │
                ├─ /bt-static/* → overlay js/css
                └─ /bt-api/* ──► API role (:8390) ──► providers
                                         │
                                         └─ SQLite cache (/app/data)
```

In legacy development bind-mount installs nginx never starts; the overlay files
are mounted into CWA and call the API on `:8390` directly (CORS applies — see
`BT_ALLOWED_ORIGINS`). The shipped helper exposes that port over HTTP and
therefore rejects HTTPS reader origins rather than creating a browser-blocked
mixed-content deployment. For cross-origin `cwa_session`, set
`authMode: 'cwa_session'` and `sendCredentials: true` in
`window.BOOK_TRANSLATOR`, disable `BT_ALLOW_PRIVATE_LAN`, and list the one exact
CWA reader origin. Token compatibility omits cookies. Managed
`authentik-forwarded` instead uses a same-origin credentialed request to the
identity-aware edge; that edge consumes the Authentik cookie and strips it
before contacting the API.

Both image roles declare `appuser` (`101:102`), run with zero capabilities, and
support a read-only root filesystem. The managed Compose installer prepares its
host bind with the just-built image in a one-shot root container, retains
private read access for the invoking operator's primary group, then keeps the
long-running API non-root. Run later lifecycle commands with that same account;
no host root shell or manual numeric `chown` is required. Unraid remains a
root-operated profile. The repository's legacy/reference Compose file uses a
Docker-managed volume.

`/ping` is liveness-only and `/health` plus `/ready` are shallow readiness
checks; none contacts an LLM. The provider-backed `/health/deep` endpoint is
protected and uses the same request budget and global provider gate as a
translation. In a managed install, call it through the public same-origin route
using the configured CWA-session or Authentik authority. Only low-level token
compatibility uses `X-BT-Token`; the private cleanup token is not a browser
credential.

`/metrics` reports request/cache counters, fixed HTTP status classes, bounded
authentication/rate-limit/provider/work-budget outcomes, partial-batch segment
failures, and bounded singleflight activity (`active_entries`, shared results,
follower timeouts, and capacity rejections). Metric dimensions are defined by
the server: routes, identities, book metadata, provider URLs, exception strings,
source text, and cache keys are never labels. Counters are process-local, which
is another reason the shipped runtime intentionally uses one gunicorn worker.

Authentication-derived tenant behavior is intentional:

- `cwa_session` isolates by the current session hash. Logging out/re-authenticating
  creates a cold tenant because CWA v4.0.6 does not expose a stable supported
  current-user JSON identity at this boundary.
- `forwarded` isolates by the stable subject asserted by an allowlisted identity
  proxy and is the mode to use when cache continuity across sessions matters.
- `token` is one shared tenant. `disabled` is one anonymous tenant and must not
  be used for production.

Release operators should follow the [Gitea-authoritative source release
runbook](docs/RELEASE.md); GitHub is a public source mirror and neither service
publishes container images.
See the [architecture overview](docs/ARCHITECTURE.md) for component details and
accepted architecture decision records. The
[production-readiness record](docs/PRODUCTION_READINESS.md) maps the latest
audit findings to their repository controls and lists the remote promotion
prerequisites that a source commit cannot establish.

## ❤️ Support the project

CWA eBook Translate is free, GPL-licensed, and has no telemetry, ads, or
subscription — if it replaced a paid translation service for you, consider
funding its development:

- **[Ko-fi](https://ko-fi.com/felixapel)** — quick one-time tips, 0% platform fees
- **[GitHub Sponsors](https://github.com/sponsors/felixapel)** — monthly support

Donations fund GPU time for multi-model testing, coverage of the 100+
language matrix, and maintainer time on issues. Non-monetary support counts
just as much: ⭐ star the repo, report bugs with reproducible steps, test new
releases, or bring a translation of the UI strings.

## 📜 License

GPL-3.0. This project extends [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
(itself GPL-licensed), and the advanced bind-mount install ships a template
derived from it — so the whole project is licensed under the GNU GPL v3 to
keep everything clean and compatible. See [LICENSE](LICENSE).

This project is not affiliated with, endorsed by, or sponsored by
Calibre-Web, Calibre-Web-Automated, Calibre, Google (Gemma), or any LLM
provider. All names are used nominatively to describe compatibility.
