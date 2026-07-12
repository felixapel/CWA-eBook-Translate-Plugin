# ADR-005: Require per-request consent for cloud fallback

- Status: Accepted
- Date: 2026-07-12

## Context

The primary provider may be a local model while the optional fallback is a
third-party cloud service. Automatically failing over would export book text
after a local outage, even though the reader selected a local deployment.
Fallback translations also have separate cache and in-flight identities.

## Decision

`POST /translate` and `POST /translate/batch` accept one additive optional JSON
boolean: `allow_cloud_fallback`. Omission is exactly `false`; every other JSON
type is rejected at the HTTP boundary. A fallback named `local` may run without
this flag. Any other fallback provider is excluded from provider calls, cache
lookup, and singleflight identities unless the current request sets the flag to
`true`.

The browser starts every reader tab with consent disabled and exposes a switch
that states book text will be sent to the configured remote provider. The
choice applies to subsequent requests in that book tab only. It is never
persisted in browser storage or supplied by the proxy bootstrap.

Configuring a cloud provider as the primary provider remains an explicit
operator-level deployment choice and is outside this fallback decision.

## Consequences

- Existing API clients remain compatible and get the privacy-safe default.
- A cached cloud-fallback result is not served to a request that has not opted
  in, so disabling consent changes both outbound and cache behavior.
- Requests with different consent values cannot coalesce into one provider
  operation.
- Users must opt in again after reloading or opening another book tab.
