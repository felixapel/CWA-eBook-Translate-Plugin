# ADR-014: Resolve configurable provider backends at the API boundary

- Status: Accepted
- Date: 2026-08-10
- Amends: [ADR-005](ADR-005-cloud-fallback-consent.md)

## Context

The original provider wrapper had fixed named endpoints and environment values
for one primary plus one fallback. It could not safely distinguish arbitrary
OpenAI-compatible endpoints in cache identity, browser privacy UI or managed
lifecycle state. Provider failures were also caught too broadly, so a terminal
authentication or request error could send the same book text to a fallback.

Consumer ChatGPT, Codex, Gemini and Antigravity subscriptions are interactive
product credentials, not server API credentials. Importing their sessions into
the translation service would expand the trust boundary and is not supported.

## Decision

The API resolves each configured role into an immutable, non-secret
`ProviderSpec`: provider ID, fixed or validated endpoint, wire protocol,
locality and cache namespace. Credentials remain separate. Named endpoints are
fixed. The custom provider ID is exactly `openai-compatible`; it requires a
dedicated key and a public HTTPS URL ending exactly in
`/v1/chat/completions`. Redirects, ambient proxies, URL credentials,
query/fragment components and non-public destinations are rejected. Its cache
namespace contains an endpoint digest, never the raw URL. Runtime resolution is
bounded by the request budget, and the TLS connection is pinned to an address
that passed the public-address check so DNS rebinding cannot redirect the call.

Primary and fallback roles have separate model, key and custom-endpoint
variables. A shared `BT_LOCAL_URL` is permitted when either role is `local`.
The default provider stays local. Identical primary/fallback configurations are
rejected.

Fallback decisions use resolved locality. Connection/DNS timeouts and HTTP
`408`, `429`, `500`, `502`, `503` and `504` may retry and fail over within the
work budget. TLS/configuration errors and terminal `4xx` responses do not.
Malformed bounded output may use a privacy-allowed fallback without blindly
retrying the same provider.

An authenticated `GET /provider-policy` returns primary/fallback locality plus
an opaque per-process generation. The reader renders active-remote warnings and
per-tab remote-fallback consent from that response and binds every translation
to it. A stale generation is rejected before cache/provider work; the reader
refetches the policy without automatically replaying book text.

`btctl reconfigure` accepts only provider-variable changes. It privately
snapshots old/new role configuration, journals digests, booleans and API
generation IDs rather than secrets or URLs, replaces only the API role, probes
every configured backend,
and atomically publishes state after verification. Failed or interrupted
cutovers restore the old API configuration. Proxy/network/data/install and
connector identities and the on-disk reader-session key are preserved.

## Consequences

- Existing installations remain local unless an operator explicitly changes
  the private environment.
- API keys from Google AI Studio/Google Cloud or another provider are supported;
  consumer subscription login reuse and project/quota sharding are not.
- CI uses provider mocks only. Real-provider latency, quota and translation
  quality remain deployment acceptance checks.
- Replacing the API process clears its in-memory five-minute session table; an
  open reader can perform one fresh native-reader exchange automatically.
