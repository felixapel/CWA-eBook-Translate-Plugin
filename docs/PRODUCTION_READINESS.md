# Production readiness record

This document records the disposition of the 2026-07-12 security and
production-readiness audit that started from the `v2.1.4` codebase and produced
the `v2.2.0` candidate. It is a promotion record, not a claim that the new
source version has already been released or deployed.

The source remediation is complete when the repository gates below pass. A
source release remains blocked until every item under
[Remote promotion prerequisites](#remote-promotion-prerequisites) is complete.

## Status vocabulary

- **Implemented and gated**: the repository contains the control and CI has a
  regression or artifact test for it.
- **Historical exception**: an immutable past artifact cannot be corrected
  without rewriting public history; the exception is documented and prevented
  for future releases.
- **Operator prerequisite**: the control lives in Gitea, the runner, a registry,
  or deployment policy and cannot be established by a source commit alone.

## Audit disposition

| Audit finding | Disposition | Repository evidence |
|---|---|---|
| F-01 unauthenticated translation | Implemented and gated | `auth.py`, `test_auth.py`, and the private API topology in `docker-compose.yml` authenticate before cache/provider work. |
| F-02 shared proxy rate-limit identity | Implemented and gated | Spoof-safe observed clients bound pre-auth attempts/inflight work; opaque authenticated subjects bound successful API quotas. Hardening contracts cover both tiers and reject forged forwarding. |
| F-03 unbounded work and storage | Implemented and gated | `work_budget.py`, provider-budget tests, mandatory cache TTL/cap, and bounded global upstream admission enforce finite work. |
| F-04 release independent of authoritative CI/parity | Implemented and gated, plus operator prerequisites | `.gitea/workflows/release.yml` validates the exact Gitea/GitHub tag and commit before all backend, browser, and container gates. Official artifacts are the validated source tag and archives. |
| F-05 divergent historical `v2.0.0` tags | Historical exception | [The release runbook](RELEASE.md#historical-split-tag) records both immutable commit identities; all future releases fail on tag divergence. |
| F-06 unprotected `main` and release tags | Operator prerequisite | Gitea branch and tag protection must be configured before promotion. |
| F-07 ambiguous segment protocol | Implemented and gated | Translation batches use unpredictable IDs and validate a strict one-to-one structured envelope. One malformed envelope gets one fresh-ID retry; a second malformed envelope uses bounded sequential single-paragraph recovery, which cannot cross-map segments and is not cached as a grouped result. |
| F-08 context-incomplete cache keys | Implemented and gated | Cache schema v2 includes tenant, book, chapter, provider/model, prompt/protocol, languages, and context fingerprints. |
| F-09 plaintext unbounded retention | Implemented and gated | Source text is not persisted in `translations_v2`, identifiers are hashed, file modes are private, and server/browser retention is bounded and opt-in where applicable. The v1 table remains physically separate only for rollback. |
| F-10 nested retries without coalescing | Implemented and gated | Absolute request budgets, bounded admission, and `singleflight.py` coalesce equivalent active work. Envelope retry and paragraph recovery spend the same atomic budget; the browser does not retry ambiguous provider work. |
| F-11 public provider-backed health probe | Implemented and gated | `/ping`, `/health`, and `/ready` are shallow; authenticated `/health/deep` uses the normal provider budget. |
| F-12 browser token in `localStorage` | Implemented and gated | The recommended topology validates the existing HttpOnly CWA session and browser loaders no longer recover a shared secret from storage. |
| F-13 unsigned, weakly reproducible supply chain | Scope reduced and gated | Official container publication was removed by ADR-008. Source identity is enforced by matching annotated tags and commits; Actions, dependencies, base inputs, and local container builds remain pinned and tested. |
| F-14 CI could skip artifact and contract checks | Implemented and gated | Docker absence is fatal; backend, frontend, Chromium, dependency, proxy, and non-root artifact gates are mandatory. |
| F-15 sensitive fallback and error leakage | Implemented and gated | Cloud fallback requires per-request consent; response sizes and error/log envelopes are bounded and sanitized. |
| F-16 privileged combined runtime | Implemented and gated | API and proxy are independent non-root roles with read-only roots, zero capabilities, and clean independent shutdown. |
| F-17 malformed JSON returned HTML 500 | Implemented and gated | API schema contracts require stable JSON 4xx responses before business logic. |
| F-18 implicit proxy authority | Implemented and gated | The proxy uses a configured public origin, fixed forwarding policy, validated upstreams, and finite body limits. |
| F-19 missing failure observability | Implemented and gated | Fixed-cardinality metrics cover authentication, admission, deadlines, provider outcomes, envelope/paragraph recovery, partial batches, and singleflight pressure without book content labels. |
| F-20 frontend state/integration drift | Implemented and gated | Real Chromium tests cover route isolation, rendering, state, rate-limit handling, consent, accessibility, and console/network health; unit contracts cover transport retries. |
| F-21 avoidable cache contention | Implemented and gated | Schema v2 uses indexed expiry/maintenance paths, WAL/busy-timeout handling, bounded maintenance, and concurrency contracts. |
| F-22 brittle limits, scripts, and metadata | Implemented and gated | Extreme numeric inputs fail closed, deployment helpers use strict quoting, dependencies/metadata are locked, and shell/workflow contracts are enforced. |
| F-23 ref and authenticity hygiene | Partially operator-owned | Protected annotated tags and exact Gitea/GitHub object parity bind official source releases. Remote branch cleanup and optional Git commit/tag signing remain deliberate maintainer operations. |
| F-24 stock Unraid lacked host Python | Implemented and gated | The public Bash `btctl` dispatcher with its embedded pinned exporter, separately verified `Dockerfile.btctl`, least-privilege mount planner, and real-Docker bootstrap smoke run the Unraid profile without host Python or NerdTools while preserving exact source identity. |

## Reproducible acceptance gate

Once the remote prerequisites are configured, protected CI is authoritative.
The maintained local command sequence is in the
[release runbook](RELEASE.md#prepare-a-release). A release candidate must prove
all of these outcomes without a skipped or unavailable gate:

1. Python compilation, standalone translation/hardening suites, and every
   backend contract suite pass.
2. The exact npm lock installs, the dependency audit is clean, and unit plus
   real-Chromium reader tests pass without console or network failures.
3. Python dependency auditing reports no known vulnerabilities.
4. The image builds and the split API/proxy smoke test proves non-root identity,
   read-only filesystems, zero capabilities, routing, independent shutdown, and
   the forwarded Authentik edge contract on a protected API route.
5. Gitea and GitHub CI definitions remain byte-identical and the workflow
   contract suites pass.
6. Release-policy contracts prove the fail-closed source workflow wiring;
   preflight rejects wrong versions, tags, commits, ancestry, or mirror parity,
   and the artifact smoke gate rejects a broken container build or runtime.
7. A real-Docker `btctl` gate proves install, state-loss adoption, conservative
   uninstall, reinstall, offline v2.1.4 migration, healthy rollback, and
   journaled re-upgrade. It also verifies that v1 and v2 cache tables coexist
   and SQLite integrity survives v1 → v2 → v1 → v2.
8. A separate real-Docker stock-Unraid gate invokes the public `./btctl` from a
   simulated host with no Python, verifies the clean full Git commit through a
   socket-free exporter, proves `plan --json` reports that exact identity, and
   completes install, doctor, and conservative uninstall through the fallback.

The 2026-07-14 local candidate audit passed Python compilation, the standalone
translation and hardening suites, the complete backend contract suite, frontend
unit tests, four real-Chromium scenarios, Python and npm vulnerability audits,
local Markdown-link/parity checks, and the split-role container build and smoke
gate on Docker 29.6.1. It predates subsequent candidate fixes and is historical
evidence, not acceptance for a newer commit; every maintained gate must run
again on the exact merged candidate. The earlier image-publication audit also
exercised multi-platform attestations before that publication path was retired
by ADR-008. The stock-Unraid bootstrap gate additionally requires a full Git
checkout but no host Python or NerdTools. Protected CI must repeat the currently
maintained gates for the exact commit that is merged; this record is not a
substitute for physical Unraid 7.3.2 and browser acceptance.

## Remote promotion prerequisites

Before creating the first post-audit version tag, an authorized operator must:

- protect Gitea `main` from direct/force pushes and require the exact backend,
  frontend, and Docker smoke contexts;
- protect `v*` tags from updates/deletion and restrict creation to the release
  operator;
- assign Docker smoke to the trusted host-capable runner;
- complete and record the single maintainer's self-review, merge through the
  protected branch with zero assumed human approvals, wait for all checks on
  the exact `main` commit, and mirror it to GitHub before creating the annotated
  tag;
- on physical stock Unraid 7.3.2 without host Python or NerdTools, run the
  public `./btctl plan`, `install`, and `doctor` path from that exact clean
  commit, then complete one real browser translation through the managed public
  route and record the commit plus result before tagging;
- publish only the annotated source tag through the Gitea-authoritative
  workflow, then build and deploy that exact checked-out tag locally.

Runner requirements, tag order, verification commands, and rollback policy are
defined in the [release runbook](RELEASE.md). No release-specific Actions
secrets are required. If any prerequisite or gate is missing, skipped, or
ambiguous, the release decision is **stop**.
