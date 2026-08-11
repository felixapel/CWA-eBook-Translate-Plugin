# ADR-017: Adaptive cloud batching and explicit replay safety

- Status: Accepted
- Date: 2026-08-11
- Amends: [ADR-014](ADR-014-configurable-provider-backends.md)

## Context

Cloud translation throughput can be constrained by requests per minute before
tokens per minute. Google documents RPM, TPM and RPD as separate
[Gemini API rate-limit dimensions](https://ai.google.dev/gemini-api/docs/rate-limits)
and defines `429 RESOURCE_EXHAUSTED` as exceeding the
[rate limit](https://ai.google.dev/gemini-api/docs/api-errors). A production
Kavita sample completed 21 batch HTTP requests and observed three Gemini `429`
responses, while the local API admitted every request. Five-paragraph
count-only groups therefore left useful model context unused and increased
exposure to project-level RPM limits.

Provider quotas apply per project and can be shared by clients this service
cannot observe. A shared SQLite limiter inside the container would coordinate
only its own processes, introduce locking and migration state, and still could
not claim project-wide correctness. Automatic replay is also unsafe after an
ambiguous transport or provider response because the provider may already have
accepted work.

## Decision

Grouping remains stable and greedy in document order. `BT_BATCH_SIZE` is the
hard paragraph-count maximum. A positive `BT_BATCH_SOURCE_TOKEN_BUDGET` adds a
deterministic approximate source-token maximum; zero preserves the historical
count-only behavior. A paragraph larger than the token budget is allowed as a
singleton so source text is never truncated. The browser keeps the first
visible request at one paragraph, then uses the managed batch maximum.

`BT_CLIENT_PREFETCH_GAP_MS` spaces only opt-in background request starts. Its
managed range is `0..10000`; batch size is `1..50`. Missing browser fields use
the historical values `5` and `0`. No new browser request concurrency is added.

Provider transport attempts, successes, failures and `429`s are exposed as
fixed process-local counters. Batch group counts and fixed size/source-token
buckets are also exposed without provider, book, identity, text or arbitrary
error labels.

Provider `Retry-After` is accepted only as positive delta seconds and capped at
30 seconds before entering sanitized per-segment metadata. A provider `429`
uses `provider_rate_limited`; it is not automatically replayed. Browser replay
is permitted only for an API HTTP `429` that explicitly states
`retry_safe: true` and an `api_admission` or `auth_admission` scope, proving the
request was rejected before provider work. Existing fields and status codes
remain compatible and the new response fields are additive.

The recommended Gemini hub example uses a batch maximum of 10, source budget
450, grouped output ceiling 1200 and background gap 1000 ms. These are bounded
starting values, not universal quota claims. Operators use `/metrics` and tune
one value at a time.

## Consequences

- More paragraphs can share each cloud request without allowing one unusually
  long group to consume an uncontrolled prompt.
- First-paint latency and visible work remain prioritized; only background
  prefetch is intentionally paced.
- `BT_BATCH_SOURCE_TOKEN_BUDGET=0` and
  `BT_CLIENT_PREFETCH_GAP_MS=0` provide an immediate scheduling rollback.
- Provider quota evidence is visible, but project-wide quota coordination and
  cross-instance fairness remain explicitly out of scope.
- A shared persistent quota coordinator may be reconsidered only if measured
  multi-process contention demonstrates value beyond the current fixed
  concurrency, batching and observability controls.
