# Universal CWA and Kavita hub

The universal hub is the simplest production topology: one immutable image,
one hardened container and one browser-facing port for each enabled stock
reader. CWA, Kavita or both can be selected without rebuilding the image.

```text
books origin  -> hub :8080 -> stock CWA
                     |-> CWA API :8391 (loopback) -> CWA cache/provider

kavita origin -> hub :8081 -> stock Kavita
                     |-> Kavita API :8392 (loopback) -> Kavita cache/provider
```

The internal processes and data are isolated by reader, but the container is a
single security and restart boundary. Keep the split topology when independent
container containment is more important than deployment simplicity or when
using CWA Authentik-forwarded identity.

## Configure

Create a private file outside the checkout:

```bash
install -d -m 0700 /absolute/private/path
cp .env.hub.example /absolute/private/path/book-translator-hub.env
chmod 0600 /absolute/private/path/book-translator-hub.env
```

Set exact state/data/backup paths, reader container names, existing Docker
networks, public origins and ports. Generate a different connector UUID for
each reader with `uuidgen`. Disable a reader with exactly `false`; variables
for a disabled reader are ignored by the runtime.

For Unraid, use `BT_INSTALL_PROFILE=unraid` and real paths below an existing
`/mnt/user/<share>/` or `/mnt/<pool>/` boundary. `btctl` creates one raw Docker
container and does not depend on the Compose plugin. For Linux Compose, keep
`compose-existing`.

Select a provider with a normal server-side API contract. A Gemini example is:

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=<Google AI Studio or project API key>
```

For a local backend use `LLM_PROVIDER=local`, leave `LLM_API_KEY` empty and set
the exact `BT_LOCAL_URL`. Per-reader overrides use
`BT_CWA_LLM_PROVIDER`, `BT_CWA_LLM_MODEL`, `BT_CWA_LLM_API_KEY` and
`BT_CWA_LOCAL_URL`, or their `BT_KAVITA_*` equivalents. Omit an override to
inherit the shared value. An explicitly empty override clears an inherited
secret. Consumer subscriptions and browser sessions are not supported API
credentials.

## Install and verify with `btctl`

```bash
./btctl plan --env /absolute/private/path/book-translator-hub.env
./btctl install --env /absolute/private/path/book-translator-hub.env --yes
./btctl doctor --env /absolute/private/path/book-translator-hub.env
```

On stock Unraid without compatible host Python/Git, the existing containerized
launcher supports plan, install, doctor and uninstall. It validates exact
storage mounts and uses the local Docker socket only for lifecycle commands.

To change providers, first run `uninstall --yes` with the current environment,
then `plan` and `install --yes` with the replacement private environment.
Uninstall removes only the verified hub container and always preserves reader
data; the next install archives the completed ownership state before committing
the new coherent generation.

An unmanaged Compose quick start is also available:

```bash
BT_HUB_ENV_FILE=/absolute/private/path/book-translator-hub.env \
  docker compose \
  --env-file /absolute/private/path/book-translator-hub.env \
  -f docker-compose.hub.yml up -d --build
```

The static Compose file declares both reader networks and ports, so use
`btctl` for a clean single-reader deployment.

## Migrate existing split deployments

Do not stop or rename containers manually. Use the private `state` directories
from the existing CWA and Kavita `btctl` installs:

```bash
./btctl migrate-topology \
  --env /absolute/private/path/book-translator-hub.env \
  --from-state /absolute/private/path/cwa-state \
  --from-state /absolute/private/path/kavita-state \
  --yes
./btctl doctor --env /absolute/private/path/book-translator-hub.env
```

Migration requires host Python 3.11+ and Git because the launcher must lock and
mount multiple independently selected source states exactly. It records every
container ID and initial running status, stops only those resources, performs
an offline SQLite checkpoint, copies each reader tree atomically and commits
schema-3 state only after the hub passes health and dependency probes. A retry
uses the durable per-reader copy manifests and recovers a hub whose state
commit completed before the journal commit.

Rollback removes only the verified owned hub, preserves its copied data and
restarts only source roles recorded as running before cutover:

```bash
./btctl rollback-topology \
  --env /absolute/private/path/book-translator-hub.env \
  --yes
```

After either cutover or rollback, open reader tabs may need one automatic
session exchange because sessions are process-local and intentionally short.

## Acceptance

- `doctor` reports every check as `ok`.
- Only the configured proxy ports are published; `8391` and `8392` remain
  loopback-only inside the container.
- CWA and Kavita each show the toolbar on a DRM-free EPUB and can translate.
- Each reader has its own `translations.db`, `reader_session_key` and cookie.
- Stopping any hub child makes the container unhealthy or exited and Docker
  restarts the complete generation.
