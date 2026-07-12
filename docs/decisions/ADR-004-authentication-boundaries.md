# ADR-004: Authenticate before deriving cache tenants

- Status: Accepted
- Date: 2026-07-12

## Context

The API previously authenticated only when an optional shared secret happened
to be configured. The proxy loader recovered that JavaScript-readable secret
from `localStorage`, and anonymous callers shared one cache tenant. CWA v4.0.6
has a login-protected JSON status route but no supported stable current-user
JSON identity suitable for cache ownership.

## Decision

Authentication is explicit and fail-closed before cache or provider work:

- `cwa_session` forwards only configured cookie names to CWA's
  exact `/ajax/emailstat` endpoint, refuses redirects or responses outside the
  bounded JSON task-list contract, coalesces duplicate probes, and derives a
  one-way per-session tenant. This is the reference same-origin proxy mode.
- `forwarded` accepts a stable subject and roles only from configured peer
  CIDRs. Directly supplied identity headers are rejected. This mode supports
  identity-aware proxies and tenant continuity across login sessions.
- `token` is compatibility mode. A non-empty secret is mandatory and all
  callers intentionally share one opaque tenant.
- `disabled` requires both an explicit mode and
  `BT_ALLOW_INSECURE_AUTH=true`; it is development/test-only.

Only `/ping`, `/health`, `/ready`, and CORS preflight bypass authentication.
Authentication attempts have a separate per-client budget. Raw cookies,
tokens, subjects, and provider error details never enter public errors or logs.
Credentialed CORS uses exact origins; subnet-wide origin matching is disabled
for CWA-session mode. The browser sends cookies only in that mode; token and
forwarded requests set Fetch credentials to `omit`.

## Consequences

- Logging out or rotating a CWA session creates a cold cache tenant. Stable
  reuse requires the `forwarded` contract because guessing a CWA user from
  page content or internal database state would create a brittle security
  dependency.
- The API role joins the CWA network for its auth probe but is not published in
  the reference Compose topology. The browser reaches it only through the
  injection proxy.
- The bundled injection proxy strips forwarded-identity headers. A `forwarded`
  deployment routes the API path directly through its allowlisted identity
  proxy, which must be the immediate peer and must have no public bypass.
- Token deployments must provision the secret out of band. The proxy loader no
  longer reads credentials from browser storage. The legacy Unraid overlay
  helper supports only CWA-session mode rather than copying a secret-bearing
  JavaScript bootstrap. Because that helper publishes the API on plain HTTP
  `:8390`, it rejects HTTPS reader origins; HTTPS deployments use the
  same-origin injection proxy or a separately reviewed TLS API route.

## Source contract

CWA v4.0.6 defines `/ajax/emailstat` with `user_login_required` and serializes
`render_task_status(...)` as a JSON list. The integration intentionally relies
on only that minimal public contract:
[upstream `tasks_status.py`](https://github.com/crocodilestick/Calibre-Web-Automated/blob/v4.0.6/cps/tasks_status.py).
