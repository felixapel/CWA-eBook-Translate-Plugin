# ADR-016: Private raw Compose environments

- Status: Accepted
- Date: 2026-08-11
- Amends: [ADR-002](ADR-002-split-non-root-runtime-roles.md)

## Context

The generated split and hub Compose JSON previously embedded the complete
runtime environment. The file was mode `0600`, but copying it for diagnosis or
review also copied provider credentials. Compose environment-file quoting is
complex: dollar signs, hashes, quotes, backslashes, spaces and equals signs can
change meaning under the default parser.

## Decision

New split installs use state schema 3. Generated Compose JSON references
installer-owned `api.env` and `proxy.env` files; hub Compose references
`hub.env`. Each file is an atomic, single-link regular file owned by the
operator with mode `0600` below a mode-`0700` state directory. Compose receives
them with `env_file.format: raw`, so validated values arrive without quoting or
interpolation. This requires Docker Compose 2.30.0 or newer and `btctl` verifies
that version before Compose validation.

The Compose document remains non-secret and deterministic. Provider-only
reconfiguration verifies it byte-semantically against the owned rendering
before journaling, recreation or recovery; only the private API environment is
rotated and rolled back.

Former schema-2 Compose documents retain their exact inline rendering for
`doctor` and conservative `uninstall`. They are never silently rewritten.
Operators uninstall and reinstall them before using provider-only
reconfiguration. Schema-1 CWA compatibility is unchanged.

## Consequences

- Routine Compose inspection no longer exposes provider credentials.
- The private environment files remain secret-bearing backup and support
  boundaries and must never be attached to issues or logs.
- Older Compose plugins fail before runtime mutation with an actionable minimum
  version error.
- Raw-file metacharacter behavior is exercised with the real Compose parser;
  lifecycle tests preserve schema-2 doctor and uninstall compatibility.
