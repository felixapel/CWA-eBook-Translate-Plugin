# Compatibility matrix

This matrix separates code-level support from configurations exercised by the
release gates. “Contract-supported” means `btctl` accepts and validates the
topology. “CI-certified” means automated tests exercise it on every candidate.
Anything else is not a promise until its acceptance checks are added.

## Calibre-Web-Automated

| Component | Status | Boundary |
|---|---|---|
| CWA 4.x | Contract-supported, Tier 1 | An exact stable version and matching running image tag/label are required. The release reference is CWA `4.0.6`; a future 4.x UI change still requires browser acceptance before promotion. |
| CWA 3.1.4 | Legacy migration only | Accepted only as the source of `btctl upgrade`; it is not a fresh v2.2 runtime target. |
| Other CWA 3.x or pre-release/mutable tags | Rejected | `btctl plan` fails before build or Docker mutation. |
| Stock CWA container | Required | The managed proxy-injection topology does not replace templates, mount overlay files into CWA, or own the CWA container. |

The project tracks the stable CWA reader contract using the
[CWA v4.0.6 reference release](https://github.com/crocodilestick/Calibre-Web-Automated/releases/tag/v4.0.6).

## Host and container runtime

| Environment | Status | Notes |
|---|---|---|
| Unraid on x86_64 | Managed and acceptance-targeted | Use `BT_INSTALL_PROFILE=unraid` as root. `btctl` creates private appdata, two DockerMan templates, and separate non-root API/proxy containers. |
| Linux with existing Compose-managed CWA | Managed and CI contract-tested | Use `BT_INSTALL_PROFILE=compose-existing`; CWA stays external to the generated private Compose document. A Docker-capable non-root account is supported when the same account and private primary group are used for every lifecycle command. |
| Docker Engine with `docker compose` plugin | Required for Compose profile | The current development audit used Docker `29.6.1` and Compose `5.3.1`. CI also builds and exercises the image on a real Docker runner. No lower minimum is claimed without a matching gate. |
| ARM64 Linux/Unraid | Not yet CI-certified | The source build may work where pinned base/package inputs resolve, but promotion requires an ARM build and runtime smoke gate. |
| Native Windows or macOS | Not a managed production target | A Linux Docker host/VM may be used, but there is no native installer or release acceptance matrix. |

Managed roles require Docker health checks, read-only root filesystems, tmpfs,
capability dropping, an internal network, and bind-mount semantics. Alternative
container engines are unsupported unless they reproduce and test those exact
contracts.

## Browser and reader

| Client | Status | Notes |
|---|---|---|
| Current Chromium | CI-certified | Real Playwright scenarios cover loader isolation, source/target selection, translation, authentication transport, accessibility, console errors, and network failures. |
| Chrome / Edge based on current Chromium | Expected compatible | Run the same public-origin acceptance checklist on the actual client before relying on it. |
| Firefox and Safari/WebKit | Not yet CI-certified | No release-blocking browser scenario currently proves them; report reproducible issues rather than assuming parity. |
| DRM-free EPUB in the CWA web reader | Supported | DRM-encrypted content cannot be parsed by CWA or this overlay. |

## Authentication and reverse proxies

| Topology | Status | Boundary |
|---|---|---|
| Native CWA session, same-origin proxy | Recommended and CI-certified | `BT_AUTH_PROFILE=cwa-session`; the API validates selected cookies against CWA and has no host port. |
| Authentik forwarded identity | Managed advanced profile | Requires `docker-edge`, exact `/32` or `/128` edge peer, a patched Authentik version, and the generated direct API route. See [AUTHENTIK.md](AUTHENTIK.md). |
| Nginx edge | Generated and contract-tested | Merge the fragment into the existing HTTPS/Authentik server configuration. SWAG and Nginx Proxy Manager still require product-specific config validation. |
| Traefik edge | Generated and contract-tested | Existing entrypoint, TLS, certificate, and Authentik settings remain operator-owned. |
| Caddy edge | Generated and contract-tested | Merge the handler inside the existing Authentik-protected site block. |
| Disabled auth, browser shared token, broad trusted subnet, published API | Rejected by managed profiles | These are not compatibility fallbacks. Fix the identity edge instead. |

## LLM providers

| Provider type | Status | Notes |
|---|---|---|
| Local OpenAI-compatible chat completions | Primary path | vLLM, Ollama, LM Studio, and llama.cpp are supported through an absolute `/v1/chat/completions` URL. `LLM_API_KEY` may remain empty. |
| OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter | Adapter-supported | Cloud credentials remain server-side. Provider export requires the reader's explicit per-tab consent where configured. Run `/health/deep` through an authenticated route before production use. |
| Arbitrary OpenAI-compatible servers | Contract-compatible, not automatically certified | They must honor the expected chat-completions request/response envelope, deadlines, and output limits. Model quality and language coverage remain model-specific. |

The CI suite uses mocked provider boundaries and never spends a real cloud key
or local GPU request. Real-provider acceptance belongs to the target deployment
and should use a short non-sensitive text before translating a book.

## Promotion rule

A configuration outside the CI-certified cells can be useful, but it is not a
release guarantee. Before declaring it supported, add a reproducible test or
record the exact host, CWA tag, browser, edge, LLM server, commands, and results
in the deployment acceptance evidence.
