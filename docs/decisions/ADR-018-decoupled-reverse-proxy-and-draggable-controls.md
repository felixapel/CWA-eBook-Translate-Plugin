# ADR-018: Decoupled reverse proxy architecture and draggable reader controls

- Status: Accepted
- Date: 2026-09-03
- Amends: [ADR-013](ADR-013-stock-reader-connectors.md), [ADR-015](ADR-015-universal-reader-hub.md)

## Context

In initial single-container hub deployments, `book-translator-hub` served both as a
reverse proxy to Calibre-Web Automated (CWA) on its proxy port (e.g. `:8385` forwarding
to `:8083`) and as the translation backend. While convenient for minimal single-host
setups, this created an operational single point of failure: if the translator hub
container stopped or restarted, reader access to CWA was immediately disrupted with
HTTP 502 errors.

Furthermore, running internal Nginx proxy workers inside the hub container when
an authoritative master reverse proxy (such as SWAG) is already deployed in front of the
homelab duplicates reverse proxy overhead and consumes redundant worker memory.

On the client UX side, fixed overlay elements obstructed reading controls on varied
mobile, tablet, and desktop viewports, requiring user-positionable reader overlays
with persistent positioning across page reloads and reading sessions.

## Decision

1. **Decoupled Reverse Proxy Integration:**
   - The master reverse proxy (SWAG) routes reader traffic directly to stock reader
     backends (`calibre-web-automated` on `:8083`, `Kavita` on `:5000`/`:5547`).
   - The master reverse proxy injects the lightweight client bootstrap loader
     (`loader.js`) via standard HTTP `sub_filter` (`</head>` injection) without
     modifying reader core files.
   - Translation API calls (`/bt-api/`) and static reader overlay scripts (`/bt-static/`)
     are routed by the master reverse proxy directly to the hub in pure API mode
     (`BT_ROLE=api` on `:8390`).
   - If `book-translator-hub` is stopped, updated, or offline, core reading and book
     browsing continue uninterrupted; only translation features degrade gracefully.

2. **Gunicorn Shared-Memory Heartbeats:**
   - In containerized environments under heavy batch translation workloads, Gunicorn
     worker heartbeat writes can experience lock contention on slow or network-backed filesystems.
   - Gunicorn invocations specify `--worker-tmp-dir /dev/shm` in `docker-entrypoint.sh`
     and `hub_runtime.py`, eliminating worker timeout false positives.

3. **Draggable Floating Controls and Position Persistence:**
   - The floating translator interface supports drag-and-drop movement via pointer and touch events.
   - Positioning state is clamped within the active viewport bounds and persisted in
     `localStorage` under key `bt_pos`.
   - Dedicated quick-position buttons (`Top`, `Bottom`, `Reset bottom`) are provided
     with localized interface text.

## Consequences

- **Fault Tolerance:** Full decoupling ensures 100% reader uptime during translator upgrades.
- **Resource Optimization:** Pure API mode (`BT_ROLE=api`, `CWA_UPSTREAM=""`) avoids running
  redundant internal proxy worker pools.
- **Reliability:** Heartbeats stored in `/dev/shm` prevent spurious worker restarts under high GPU load.
- **Client Usability:** Reading overlays are adjustable to reader layouts on both mobile and desktop screens.
