# ADR-011: Bootstrap the Unraid operator through Docker

- Status: Accepted
- Date: 2026-07-14

## Context

`BT_INSTALL_PROFILE=unraid` is a first-class managed profile, but stock Unraid
7.3.2 does not include Python. The original public `btctl` and its helper
modules used Python shebangs, so a non-expert following the documented Unraid
path failed before configuration could be validated. Requiring NerdTools would
add an unrelated host prerequisite and make the source-only installation less
portable.

The project still has to prove that a local production image came from one
exact clean commit. Giving an unverified image a bind-mounted Docker socket
would defeat that check because the Docker socket is equivalent to root access.
The production API/proxy image must also remain a small, non-root runtime rather
than becoming an administration container.

## Decision

- `./btctl` remains the only public lifecycle command. When compatible host
  Python and Git are available it directly runs the internal Python CLI.
  Otherwise its strict Bash dispatcher uses Docker automatically for the
  Unraid profile.
- The fallback requires root, Bash, Docker, and a full Git checkout including
  `.git`. Host Python, host Git, and NerdTools are not required to execute it.
  A Git-capable machine or coding agent may prepare and copy the complete
  checkout; a source ZIP or tarball alone is insufficient.
- The public launcher embeds the complete digest- and package-pinned
  source-exporter definition. It builds that definition with an empty temporary
  context before consulting any Dockerfile from the checkout. The exporter
  receives only the checkout read-only, with no network and no Docker socket.
  It disables Git replacement refs, fsmonitor, and untracked-cache extensions;
  validates the top-level repository, clean tracked and untracked state,
  SemVer, and full commit SHA; then streams `git archive` for that exact SHA.
- The archive builds a separate, revision-labeled operator image containing
  only the installer modules, Python, Git, and Docker CLI. The dispatcher
  verifies the resulting immutable image ID and revision label before use.
- A containerized planner validates the private environment and emits a
  versioned mount protocol. The dispatcher accepts only the documented command
  matrix, existing `/mnt/user/<share>/...` or `/mnt/<pool>/...` roots, and the
  exact DockerMan template directory. One empty root-owned mode `0700`
  directory at `/run/cwa-translate-btctl-locks` supplies a stable global lock
  inode to both native and containerized Unraid commands. It is bound read-only
  at `/run/btctl-lock`, so serialization exposes no sibling appdata. Broader ancestor
  binds needed to create state or stage an upgrade are narrowed by nested
  read-only guards. `plan` and `auth-snippet` receive no Docker socket; other
  commands receive the local `/var/run/docker.sock` and only their required
  read-only or read-write paths.
- Temporary containers run without network access, with a read-only root,
  bounded processes and tmpfs, dropped capabilities, and
  `no-new-privileges`. Socket-free readers regain only `DAC_READ_SEARCH` so a
  root launcher can inspect a valid private checkout/environment owned by the
  user who prepared it. The production Dockerfile is unchanged: API and proxy
  still run as uid `101`, gid `102` without Git or Docker administration tools.

## Alternatives considered

### Require Python through NerdTools

Rejected because the primary Unraid path should work on the stock platform and
should not depend on an additional community plugin solely to parse a plan.

### Rewrite the complete lifecycle in POSIX shell

Rejected because it would duplicate mature validation, state, migration, and
Docker logic in a language less suited to the existing structured contracts.

### Give a project image the checkout and Docker socket directly

Rejected because an image built from unverified working-tree bytes would gain
root-equivalent host authority before clean-source identity was established.

## Consequences

- The first no-Python invocation, including `plan`, can fetch pinned base
  layers, build and remove temporary images, and warm Docker build cache. It
  does not create deployment state, modify CWA, or create production runtime
  resources.
- A full checkout consumes more space than a release archive and must be copied
  with `.git` intact when prepared elsewhere.
- Unraid lifecycle mutations are serialized globally on the host. The coarse
  lock favors safe recovery over concurrent management of separate translator
  installs and disappears with the normal `/run` tmpfs at reboot.
- The public launcher and its embedded exporter definition are the unavoidable
  bootstrap trust root. They must come from a trusted reviewed commit; running
  a malicious replacement launcher as root cannot be made safe by checks that
  launcher performs itself. `Dockerfile.btctl` is consumed only from the
  exporter-verified archive. CI therefore checks Bash syntax, synchronization
  of all pinned exporter inputs, exact copied files, command/mount policy,
  production-image separation, and a real-Docker smoke test that runs with no
  host Python.
- Compose installations retain the native Python requirement when the host
  cannot use the Unraid fallback; broadening the bootstrap to other profiles
  requires a separate compatibility decision and gate.
