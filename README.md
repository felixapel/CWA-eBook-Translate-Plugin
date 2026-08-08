# CWA eBook Translate

CWA eBook Translate adds bilingual, paragraph-level LLM translation to the
[Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
reader without modifying the CWA image.

![Bilingual reading demo](docs/assets/demo.gif)

## What it does

- Shows original and translated text together, or either one by itself.
- Offers 100+ source and target language choices. Translation quality depends
  on the selected model and language pair.
- Prioritizes visible paragraphs; whole-chapter prefetch is an explicit opt-in.
- Supports local OpenAI-compatible servers and optional cloud providers.
- Keeps provider credentials server-side and requires explicit consent before
  a configured local provider falls back to a cloud provider.
- Uses a private, bounded SQLite cache scoped by authenticated reader context.
- Preserves stock CWA and keeps the translation API off the host network in
  managed installations.

## Supported installation

The production path is `btctl`. It builds an immutable local image from an
exact clean release checkout and manages a browser-facing proxy plus a private
API role. Use the current annotated release:

```bash
git clone https://github.com/felixapel/CWA-eBook-Translate-Plugin.git cwa-translate
cd cwa-translate
git fetch --tags
git switch --detach v2.2.2
```

Copy the managed configuration outside the checkout and make it private:

```bash
install -d -m 0700 /absolute/private/path
cp .env.example /absolute/private/path/cwa-translate.env
chmod 0600 /absolute/private/path/cwa-translate.env
```

Set the exact CWA container, network, version, public origin, storage paths and
LLM endpoint. A local provider normally leaves `LLM_API_KEY` empty. Then run:

```bash
./btctl plan --env /absolute/private/path/cwa-translate.env
./btctl install --env /absolute/private/path/cwa-translate.env --yes
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

`plan` validates and reports the intended resources without changing CWA or
deployment state. `install` commits state only after live postconditions pass.
`doctor` is read-only and every check must report `ok`.

Stock Unraid requires root, Bash, Docker and a full checkout including `.git`.
It does not require host Python, host Git or NerdTools to run `./btctl`; the
launcher uses a temporary containerized operator when needed. Linux hosts with
an existing Compose-managed CWA use `BT_INSTALL_PROFILE=compose-existing`.

Community Applications uses a separate combined-image profile. Install it only
from a searchable listing whose template pins an immutable image digest; if no
listing is present, use `btctl`. See the
[Community Applications guide](docs/install/community-applications.md).

## Runtime boundary

```text
Browser / reverse proxy -> injection proxy -> stock CWA
                                  |
                                  +-> private translation API -> LLM
                                                     |
                                                     +-> SQLite cache
```

The recommended `cwa-session` profile validates the reader's existing CWA
session. CWA strong session protection is supported in the managed topology by
preserving the browser User-Agent and proxy-observed address context. Do not
publish the API, disable authentication, or add a route that bypasses the
managed proxy. Advanced Authentik deployments have a separate fail-closed
profile and guide.

## Documentation

- [Documentation map](docs/README.md)
- [Managed `btctl` install](docs/install/btctl.md)
- [Community Applications](docs/install/community-applications.md)
- [Authentik integration](docs/install/authentik.md)
- [Lifecycle and recovery](docs/operations/lifecycle.md)
- [Troubleshooting](docs/operations/troubleshooting.md)
- [Compatibility](docs/reference/compatibility.md)
- [Configuration](docs/reference/configuration.md)
- [Architecture](docs/reference/architecture.md)

## Development and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[development guide](docs/maintainers/development.md) before changing the
project. Use the issue templates for reproducible bugs and feature proposals.
Report vulnerabilities through the private channel in [SECURITY.md](SECURITY.md).

CWA eBook Translate is GPL-3.0 software with no telemetry, ads or subscription.
Support is optional through [Ko-fi](https://ko-fi.com/felixapel) or
[GitHub Sponsors](https://github.com/sponsors/felixapel). The project is not
affiliated with or endorsed by CWA, Calibre, Google or any LLM provider.

See [LICENSE](LICENSE) for the license text.
