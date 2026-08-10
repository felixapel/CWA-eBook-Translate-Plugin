# ADR-015: Universal one-container reader hub

- Status: Accepted
- Date: 2026-08-10
- Amends: [ADR-002](ADR-002-split-non-root-runtime-roles.md)

## Context

A dual-reader installation previously needed four translator containers: one
API and one injection proxy for each of CWA and Kavita. That maximized process
and restart isolation, but multiplied configuration, health checks, upgrades,
state paths and Unraid deployment work. The API code also reads configuration
at module import, so two readers cannot safely share one Gunicorn process.

## Decision

The ordinary recommended topology is one `BT_ROLE=hub` container. It starts one
Gunicorn process per enabled reader and one nginx master with a namespaced
listener per reader. The two API sockets bind only to loopback. CWA uses
listener `8080`/API `8391`; Kavita uses listener `8081`/API `8392`.

Each reader retains a separate SQLite database, session signing key, connector
UUID, cookie name, browser contract and provider environment under
`/app/data/<reader>`. Shared `LLM_*` values are defaults; explicit
`BT_CWA_*` or `BT_KAVITA_*` provider values replace them for only that reader.
Hub-wide concurrency is split fairly unless every enabled reader receives an
explicit allocation that fits within the total.

`BT_ENABLE_CWA` and `BT_ENABLE_KAVITA` select either reader or both. Each
enabled reader has its own browser-facing published port because origins,
reader cookies and upstream routes must remain unambiguous. One child exit
terminates the complete container; Docker restarts a coherent generation.

`btctl` schema 3 owns one hub container and preserves stock readers as external
resources. Its topology migration stops exact schema-1/2 source roles,
checkpoints SQLite, atomically copies each reader tree, commits a durable
journal and can restore the exact previously-running source containers.

## Security boundary

One container is one compromise and restart boundary. Separate Unix processes,
loopback sockets, namespaced nginx variables, cookies and files prevent normal
cross-reader configuration leakage, but they do not provide the kernel-level
isolation of separate containers. Operators who need independent containment
or restarts should retain ADR-002's split topology.

CWA Authentik-forwarded mode remains split-only. Its reviewed identity edge
must route directly to a separately addressable API and must not be emulated by
trusting browser-supplied headers inside the hub. The hub supports only the
short-lived `reader-session` exchange.

## Consequences

- Ordinary CWA-only, Kavita-only and dual-reader installs deploy one image and
  one long-lived container.
- A failure or provider reconfiguration restarts every enabled reader process;
  open tabs may perform a fresh short-lived session exchange.
- Two published proxy ports remain necessary even though there is one
  container. Internal API ports are never published.
- Legacy split states remain readable and reversible; they are not silently
  rewritten to schema 3.
- Consumer ChatGPT, Codex, Gemini or Antigravity subscriptions are not backend
  credentials. Only documented API keys or OpenAI-compatible endpoints enter
  the server-side environment.

## Verification

Hub unit contracts cover provider allocation, reader-specific cookies and
paths, one-container Compose rendering, schema-3 state, fail-fast supervision,
ownership verification and rollback evidence. The production image smoke must
also prove both proxy listeners and databases in one non-root sandbox.
