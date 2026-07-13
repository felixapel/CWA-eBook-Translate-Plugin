# ADR-009: Keep cache schemas side by side for rollback

## Status

Accepted

## Date

2026-07-13

## Context

The first unreleased schema-v2 implementation renamed the v1 `translations`
table to `translations_v1` and reused `translations` for v2. The old rows were
preserved, but checking out `v2.1.4` against that database was not a functional
rollback: the old code queried and wrote `translations`, now a v2 table without
the required `source_text` column.

A reverse conversion cannot preserve v2 rows as v1. Schema v2 intentionally
does not store source text, and its cache key includes tenant, book, chapter,
context, provider, prompt, and protocol dimensions that v1 cannot represent.

## Decision

- Keep the v1 table named `translations` and never serve it through v2.
- Store all schema-v2 rows in `translations_v2` in the same SQLite database.
- Normalize the unreleased draft layout atomically: rename its v2 table to
  `translations_v2` and restore `translations_v1` to `translations`.
- Fail closed on unknown or ambiguous table layouts.
- Require an offline `/app/data` snapshot before upgrading production even
  though the side-by-side layout supports an in-place code rollback.

## Alternatives considered

### Rename v1 and reuse `translations`

Rejected because the previous release starts against the wrong schema and its
first cache write fails.

### Reverse-migrate v2 rows to v1

Rejected because the source text and v2 scope dimensions cannot be reconstructed.

### Store v2 in a separate database file

Rejected because it changes the public `DB_PATH` deployment contract and
duplicates connection, backup, and file-hardening behavior. Separate tables
provide the required rollback boundary inside the existing private database.

## Consequences

- `v2.1.4` can read and write its original table after v2 has run, while v2
  retains its own rows for a later re-upgrade.
- Rolling back loses no v1 cache data; the older release simply ignores
  `translations_v2`.
- Cache files still require an offline snapshot before upgrade to protect
  against storage, operator, or unrelated migration failures.
- The reference v2.1.4 Compose bind mount must be copied offline into the v2.2
  named volume. The untouched bind, external snapshot, and stopped old
  container remain the rollback authority until the operator accepts v2.2.
- The compatibility contract must exercise v1 → v2 → v1 → v2 and SQLite
  integrity, including the unreleased draft layout.
