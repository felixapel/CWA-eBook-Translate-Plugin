# ADR-006: Make proxy authority and forwarding explicit

- Status: Accepted
- Date: 2026-07-12

## Context

The injection proxy previously forwarded the request `Host`, accepted any
inbound `X-Forwarded-Proto`, extended any inbound `X-Forwarded-For` chain, and
disabled CWA request-body checking with `client_max_body_size 0`. Those defaults
made deployment convenient but left public URL generation, secure-cookie
decisions, client identity, and buffered upload disk use under request-controlled
or unbounded inputs.

nginx documents that `proxy_set_header` explicitly defines upstream header
values, that `$proxy_add_x_forwarded_for` appends to the received client header,
and that a `client_max_body_size` of zero disables body-size checking:

- <https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header>
- <https://nginx.org/en/docs/http/ngx_http_proxy_module.html#var_proxy_add_x_forwarded_for>
- <https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size>

## Decision

Proxy and combined roles require `BT_PUBLIC_ORIGIN`, an exact HTTP(S) origin.
A standard-library renderer validates and normalizes that origin, both upstream
base URLs, the listen port, UI version, and the finite CWA upload size before it
atomically writes a private nginx configuration. Invalid values stop startup
with `EX_CONFIG` and are never copied into logs.

Both upstream locations:

- set `Host`, `X-Forwarded-Host`, and `X-Forwarded-Proto` from the validated
  public origin;
- remove the standardized `Forwarded` header and any forwarded port;
- replace `X-Forwarded-For`/`X-Real-IP` with nginx's observed `$remote_addr`;
- use relative nginx-generated redirects (`absolute_redirect off`);
- retain bounded connection/read timeouts.

The CWA location also removes `BT_CWA_IDENTITY_HEADER` (default
`Remote-User`). CWA treats its configured reverse-proxy header as a complete
login assertion, while the bundled injection proxy is directly browser-facing
and is not an identity authority. Operators using a custom CWA header must set
the same name here; header-based SSO belongs behind a separate trusted identity
proxy rather than this injection boundary.

CWA uploads default to a finite `2g` cap through `BT_CWA_MAX_BODY_SIZE`; API
bodies retain their smaller proxy and Flask caps. gettext/envsubst is removed
from the image because unvalidated textual substitution is no longer used.

## Consequences

- Reverse-proxy deployments must set their exact HTTPS reader origin instead
  of relying on a request header. This is an intentional fail-closed migration.
- Direct clients retain accurate per-client API limits. Behind another edge
  proxy, the translator sees that immediate peer as one client unless admission
  is enforced at the trusted edge; it never guesses trust from a supplied chain.
- An operator can raise the CWA upload ceiling for a known library, but cannot
  configure an unlimited body through this interface.
- A mismatched CWA reverse-proxy header name is an unsafe deployment error;
  operators must keep `BT_CWA_IDENTITY_HEADER` aligned or disable CWA's
  reverse-proxy-header login.
- The renderer adds isolated validation tests and is compiled in CI/release;
  the container smoke test checks the rendered authority and forwarding rules.

## Verification

`test_proxy_config.py` exercises valid rendering plus origin, upstream URL,
port, version, and size rejection without exposing rejected values.
`test_container_contract.py` pins the static trust boundary, while
`scripts/container-smoke.sh` inspects the actual rendered configuration inside
the read-only non-root proxy container.
