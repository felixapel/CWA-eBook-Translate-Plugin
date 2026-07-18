# Configuration reference

Managed operators should copy [`.env.example`](../../.env.example) outside the
checkout and edit only that copy. `btctl` validates the file, derives internal
role settings and redacts secrets from its output. Never commit the edited file.

## Managed installation inputs

| Variable | Purpose |
|---|---|
| `BT_INSTALL_PROFILE` | `unraid` or `compose-existing`. CWA always remains external. |
| `BT_INSTALL_NAME` | Stable prefix for owned translator resources. Choose once. |
| `BT_INGRESS_MODE` | `published` exposes only the proxy; `docker-edge` publishes neither role. |
| `BT_PROXY_PORT` | Host port for the proxy in `published` mode. |
| `BT_EDGE_NETWORK` | Existing edge network required by `docker-edge`. |
| `BT_AUTH_PROFILE` | `cwa-session` by default; `authentik-forwarded` only for the documented advanced topology. |
| `BT_PUBLIC_ORIGIN` | Exact browser-facing origin, including scheme and optional port. |
| `CWA_UPSTREAM` | Must be `http://<BT_CWA_CONTAINER>:8083`. |
| `BT_CWA_CONTAINER` | Exact running stock CWA container name. |
| `BT_CWA_NETWORK` | One existing Docker network joined by CWA. |
| `BT_CWA_VERSION` | Exact stable CWA version observed by the install. |
| `BT_CWA_IDENTITY_HEADER` | Exact reverse-proxy identity header configured in CWA; the managed proxy strips client copies. |
| `BT_STATE_DIR` | Private lifecycle state outside the checkout. |
| `BT_DATA_DIR` | Private translation data outside the checkout. |
| `BT_BACKUP_DIR` | Backup root outside appdata/data. |
| `BT_UNRAID_TEMPLATE_DIR` | DockerMan user-template directory for the Unraid profile. |
| `LLM_PROVIDER` | `local` or a supported cloud adapter. |
| `LLM_MODEL` | Provider model identifier. |
| `BT_LOCAL_URL` | Absolute OpenAI-compatible `/v1/chat/completions` endpoint for `local`. |
| `LLM_API_KEY` | Provider credential; leave empty for an unauthenticated local server. |
| `BT_IDENTITY_PROXY_IP` | Exact `/32` or `/128` trusted identity peer for Authentik mode. |
| `BT_AUTHENTIK_VERSION` | Exact accepted Authentik version for the advanced profile. |
| `BT_AUTHENTIK_OUTPOST_URL` | Private outpost URL for generated edge configuration. |
| `BT_REVERSE_PROXY` | Supported edge type for `auth-snippet`. |
| `BT_LEGACY_CONTAINER` | Exact combined legacy container used only by `upgrade`. |
| `BT_LEGACY_DATA_DIR` | Its exact bind-mounted data directory. |

## Translation runtime settings

These variables are the low-level image interface. Managed installs derive
authentication, role, network and browser settings; override tuning values only
with measured evidence.

| Variable | Default | Boundary |
|---|---:|---|
| `BT_ROLE` | `auto` | `api`, `proxy` or compatibility-only `all`. Managed installs set roles explicitly. |
| `PORT` | `8390` | API listen port inside the container. |
| `BT_PROXY_PORT` | `8080` | Proxy listen port inside the container. |
| `BT_CWA_MAX_BODY_SIZE` | `2g` | Finite nginx upload limit for CWA traffic. |
| `BT_MAX_CONCURRENT` | `2` | Group workers inside one batch request. |
| `BT_BATCH_SIZE` | `5` | Paragraphs per grouped provider call. |
| `BT_MAX_TOKENS` | `4096` | Single-paragraph output ceiling. |
| `BT_BATCH_MAX_TOKENS` | `8192` | Grouped output ceiling. |
| `BT_OUTPUT_TOKEN_FACTOR` | `2.0` | Proportional output cap. |
| `BT_OUTPUT_TOKEN_FLOOR` | `256` | Minimum requested output tokens. |
| `BT_CONTEXT_WINDOW` | `0` | Surrounding paragraphs included as non-translated context. |
| `BT_TIMEOUT` | `60` | Per-provider-call timeout in seconds. |
| `BT_MAX_UPSTREAM_INFLIGHT` | `2` | Process-wide provider concurrency cap. |
| `BT_UPSTREAM_QUEUE_TIMEOUT` | `2` | Wait for a provider slot before `503`. |
| `BT_MAX_UPSTREAM_RESPONSE_BYTES` | `1048576` | Maximum decompressed provider response. |
| `BT_REQUEST_MAX_ATTEMPTS` | `20` | Total provider calls, including bounded malformed-envelope recovery. |
| `BT_REQUEST_MAX_INPUT_BYTES` | `5000000` | Cumulative prompt bytes per API request. |
| `BT_REQUEST_MAX_OUTPUT_TOKENS` | `163840` | Cumulative reserved output tokens per API request. |
| `BT_REQUEST_DEADLINE_SECONDS` | `90` | Absolute request deadline. |
| `BT_SINGLEFLIGHT_MAX_ENTRIES` | `1024` | Active deduplicated operation cap. |

A malformed grouped response receives one fresh-envelope retry. If it remains
malformed, paragraphs are recovered sequentially inside the same finite request
budget. Successful paragraph recovery is not written under the grouped cache
contract. Ordinary paragraph failures are returned per paragraph; exhausted
work budgets fail the request before starting more provider work.

## Authentication and network settings

| Variable | Default | Boundary |
|---|---:|---|
| `BT_AUTH_MODE` | `token` | `cwa_session`, `forwarded`, `token` or development-only `disabled`. |
| `BT_ALLOW_INSECURE_AUTH` | `false` | Second acknowledgement required for `disabled`; never use in production. |
| `BT_API_TOKEN` | empty | Required by token mode; shared tenant and compatibility use only. |
| `BT_CWA_AUTH_URL` | empty | Exact CWA `/ajax/emailstat` URL for session validation. |
| `BT_CWA_AUTH_COOKIE_NAMES` | `session,remember_token` | Only cookies permitted to leave the API for the CWA probe. |
| `BT_CWA_AUTH_TIMEOUT_SECONDS` | `2` | Probe timeout. |
| `BT_CWA_AUTH_CACHE_TTL_SECONDS` | `15` | Short positive and negative validation-cache TTL. |
| `BT_CWA_AUTH_CACHE_MAX_ENTRIES` | `10000` | Validation-cache cap. |
| `BT_CWA_AUTH_MAX_INFLIGHT` | `8` | Distinct concurrent CWA probe cap. |
| `BT_CWA_AUTH_MAX_RESPONSE_BYTES` | `262144` | Maximum decompressed CWA probe response. |
| `BT_IDENTITY_TRUSTED_PROXIES` | empty | Exact trusted peers for forwarded identity. |
| `BT_AUTH_RATE_LIMIT_PER_MINUTE` | `300` | Pre-authentication attempts per observed client. |
| `BT_RATE_LIMIT_PER_MINUTE` | `120` | Successful API requests per authenticated subject. |
| `BT_RATE_LIMIT_RETRY_AFTER` | `10` | `Retry-After` value returned on `429`. |
| `BT_RATE_LIMIT_MAX_CLIENTS` | `10000` | Active limiter-bucket cap. |
| `BT_TRUSTED_PROXIES` | empty | Reviewed peers allowed to provide observed client context. |
| `BT_TRUSTED_PROXY_HOST` | empty | Managed single proxy authority for strong CWA sessions. |
| `BT_TRUST_PROXY` | `false` | Unsafe legacy rate-limit compatibility; not an auth authority. |
| `BT_ALLOWED_ORIGINS` | local defaults | Exact origins for low-level cross-origin deployments. |
| `BT_ALLOW_PRIVATE_LAN` | `true` | Broad private-origin convenience for non-cookie modes only. |

The managed proxy replaces inbound forwarding chains with the peer it observed.
After authentication, quotas and cache scope use an opaque server-owned subject,
not a request-supplied address or header.

## Cache and fallback settings

| Variable | Default | Boundary |
|---|---:|---|
| `DB_PATH` | `translations.db` | SQLite path; production images use `/app/data/translations.db`. |
| `BT_CACHE_TTL_DAYS` | `90` | Mandatory expiry. |
| `BT_CACHE_MAX_ENTRIES` | `100000` | Mandatory row cap. |
| `BT_CACHE_HIT_FLUSH_THRESHOLD` | `100` | Batched hit-counter flush threshold. |
| `BT_CACHE_HARDEN_EXISTING_DIR` | `false` | Production image enables private existing-directory checks. |
| `LLM_FALLBACK_PROVIDER` | empty | Optional secondary provider. |
| `LLM_FALLBACK_MODEL` | empty | Secondary model. |
| `LLM_FALLBACK_API_KEY` | empty | Secondary credential. |

Remote fallback is fail-closed: each `POST /translate` or
`POST /translate/batch` request must set the JSON boolean
`allow_cloud_fallback: true` before book text can leave a local primary path.
The reader stores that choice only in the current tab. Choosing a cloud provider
as primary is a separate operator decision and sends ordinary requests there.

Cache schema v2 stores translations plus scoped one-way hashes, not source
paragraphs, CWA cookies or raw user/book identifiers. The legacy v1 table stays
side by side for rollback but is never read by v2.
