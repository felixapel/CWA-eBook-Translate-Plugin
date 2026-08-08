# Architecture Overview

This document details the architecture of the `book-translator` plugin.

## Architecture decisions

The indexed [architecture decision records](../decisions/README.md) preserve
the security, release, cache and deployment rationale, including superseded
decisions.

## Overview

The plugin operates as a decoupled overlay integrated into
Calibre-Web-Automated (CWA). There are two production deployment profiles:

1. **Managed split profile (`btctl`, recommended).** Two isolated non-root
   containers run the same release image with `BT_ROLE=proxy` and
   `BT_ROLE=api`. nginx sits in front of a **stock** CWA instance
   (`CWA_UPSTREAM`). HTML responses get a
   single `<script src="/bt-static/loader.js">` tag injected before `</head>`;
   `loader.js` self-guards to `/read/` pages and loads the overlay. The API is
   reachable same-origin under `/bt-api/`, so CORS never applies. Because only
   one tag is injected (instead of maintaining a forked `read.html`), CWA
   template updates cannot silently break or drop the plugin. See
   `proxy/nginx.conf.template` and `docker-entrypoint.sh`. The proxy passes the
   browser's HttpOnly CWA cookie to the API; the API validates only configured
   cookie names against CWA's authenticated JSON probe and derives an opaque
   per-session tenant. `BT_PUBLIC_ORIGIN` fixes the forwarded host/scheme;
   inbound forwarding headers are discarded, the observed peer becomes the
   only forwarded client hop, and CWA uploads have an operator-configurable
   finite body cap.
2. **Community Applications combined profile (listing-gated).** One non-root
   container runs `BT_ROLE=all`, exposes only its proxy port and keeps API port
   `8390` private. This is production-supported only when the searchable
   listing pins a certified immutable image digest and the host matches the
   [documented boundary](../install/community-applications.md). Otherwise use
   `btctl`.

`overlay/read.html` remains a legacy development fixture for investigating CWA
template compatibility. Mounting it into CWA and publishing the API
cross-origin is not a supported production installation method.

The diagram below shows the recommended split data flow. The proxy is the only
browser-facing translator role; the API owns the writable SQLite volume. The
combined CA profile preserves the same logical boundary inside one container.

```
Browser ──► proxy role (:8080) ──► CWA (:8083, stock)
                │
                └── /bt-api/* ──► API role (:8390) ──► LLM provider
                                         │
                                         └── SQLite volume (/app/data)
```

## Deployment control plane

`btctl` validates a strict environment file and derives an immutable local
image identity from `VERSION` plus the clean checkout SHA. Its deterministic
`plan` declares every resource and its ownership before Docker is touched.
Lifecycle state is private, atomic, schema-versioned, and contains no secrets.
This separates source, configuration, mutable translation data, and backups so
an update of one cannot silently replace another.

The Compose adapter writes a private JSON-form Compose document (JSON is a
Compose-compatible YAML subset), validates it with the local Compose plugin,
and starts only the two translator services. Live image IDs, installation
labels, networks, health, and port bindings must match before state is
committed. Recovery adoption is read-only with respect to Docker and requires
the same pre-existing evidence. Migration recovery performs that adoption
before it can preserve or rename a target tree, so a crash after runtime start
cannot detach an active API container from its bind source.

## Component Breakdown

### Frontend (`translator.js`)
- **Lifecycle Observers**: Hooks into CWA reader using iframe document checking and `epub.js` rendition hooks (`relocated`, `rendered`).
- **Translation Management**: Coordinates visible-first translation chunking;
  background sequential whole-chapter prefetch is disabled until the reader
  explicitly enables it.
- **Client Cache**: Keeps context-scoped translations in memory. Durable
  `localStorage` is an explicit opt-in for trusted single-user browsers; keys
  include release, languages, book, chapter, and stable DOM position so
  repeated text in different literary contexts cannot collide.

### Backend (`book-translator-api`)
- **Authentication (`auth.py`)**: Fails closed in token, CWA-session, or
  trusted-forwarded mode before any cache/provider work. Raw credentials and
  subjects become opaque hashes; CWA checks require the exact protected task
  endpoint and bounded JSON-list shape, are TTL/cap bounded, and coalesce
  concurrent duplicates. The frontend attaches cookies only in CWA-session
  mode.
- **Flask Server (`server.py`)**: Exposes translation endpoints `/translate`
  and `/translate/batch` along with metrics and health probes. Only shallow
  liveness/readiness routes bypass authentication. Observability uses a fixed
  schema for HTTP classes and bounded auth, admission, provider, deadline, and
  partial-batch outcomes; it never creates labels from request or book data.
- **SQLite Cache (`cache.py`)**: Schema v2 keys include tenant, book, chapter,
  provider, model, prompt/protocol fingerprint, group context, languages, and
  source hash. Source paragraphs and raw tenant/book/chapter identifiers are not
  stored. TTL/cap are mandatory and group hits are atomic, so cached paragraphs
  cannot alter the context seen by a later provider call.
- **LLM Client (`translator.py`)**: Multi-provider wrapper that supports batch
  translation prompts with dynamic context windows (`BT_CONTEXT_WINDOW`).
  Remote fallback providers require explicit consent on each request; requests
  with different consent policies never share cache lookup or in-flight work.
