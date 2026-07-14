# Troubleshooting

These checks apply to the managed v2.2.0 split deployment. Do not expose the
API, add a browser token, broaden a trusted proxy range, or disable
authentication to make an error disappear.

## Start with the deployment evidence

Run the read-only doctor from the same clean checkout and private environment
file used to install:

```bash
./btctl doctor --env /absolute/private/path/cwa-translate.env
```

For structured output that is easier to share after removing private paths:

```bash
./btctl doctor --env /absolute/private/path/cwa-translate.env --json
```

Every check must be `ok`. Doctor validates the saved plan, version+commit image,
owned containers/network, exact CWA evidence, health, runtime environment,
authentication profile, published ports, and generated artifacts. Fix its first
failed check before debugging the browser.

## Toolbar is missing

1. Open the reader through `BT_PUBLIC_ORIGIN`, not CWA's direct port. Stock CWA
   intentionally has no overlay.
2. Hard-refresh once (`Ctrl+Shift+R` or `Cmd+Shift+R`).
3. In Browser DevTools, confirm `GET /bt-config.json` returns `200`, a JSON
   object, and `Cache-Control: no-store`. A missing or invalid managed config
   makes the loader fail closed; page variables cannot override it.
4. Confirm the reader page loads `/bt-static/loader.js` and that the Console
   has no `[BookTranslator]` error.
5. If a reverse proxy is present, confirm its main CWA route points to the
   managed injection proxy. Keep OPDS/Kobo routes pointed directly at CWA.

## Translation requests return 401 with `cwa-session`

The default profile accepts a native CWA session, not merely an Authentik edge
cookie. Sign out and back into CWA, then verify the browser sends the CWA
session cookie to the same public origin.

If Authentik authenticates the browser but CWA never creates a session that its
`/ajax/emailstat` endpoint accepts, use the separate
`authentik-forwarded` topology in [AUTHENTIK.md](AUTHENTIK.md). Do not switch to
anonymous mode and do not put a shared secret in browser storage.

A `503` instead of `401` means the API could not safely evaluate CWA as the
authority. Check that the CWA container is running, `CWA_UPSTREAM` and
`BT_CWA_NETWORK` are exact, and the selected auth endpoint is reachable from
the API container. `doctor` catches topology and runtime drift; API logs contain
the bounded authority failure without session-cookie contents.

## Translation requests return 401 with `authentik-forwarded`

Check all of these as one security contract:

- the request enters the edge's generated `/bt-api/` route;
- the Authentik outpost URL is reachable and the configured version is patched;
- the edge overwrites `X-authentik-uid` from the outpost response;
- the edge removes `Cookie`, `X-BT-Subject`, and `X-BT-Roles` before the API;
- the live edge address on `BT_EDGE_NETWORK` exactly matches
  `BT_IDENTITY_PROXY_IP` as `/32` or `/128`;
- neither translator role publishes a host port.

An edge-container IP change intentionally causes a fail-closed `401`. Restore
the reserved address or use the old matching environment to run managed
`uninstall`, then edit the peer and run `plan`, `install`, and `doctor`. The
completed uninstall evidence is archived and translation data is preserved.
Never replace the exact peer with an entire Docker subnet. Regenerate the
reviewed fragment with:

```bash
./btctl auth-snippet --env /absolute/private/path/cwa-translate.env
```

## Toolbar loads but translation fails

- Source and target must differ. Choose the book language in Settings and the
  output language in the toolbar.
- In DevTools Network, inspect the JSON error from `/bt-api/translate/batch`.
  The browser should call a same-origin relative route, not a host API port.
- Inside Docker, `localhost` means the API container. Set `BT_LOCAL_URL` to an
  address reachable from that container, normally the LLM host's LAN address.
- Local OpenAI-compatible endpoints for vLLM, Ollama, LM Studio, and llama.cpp
  normally end in `/v1/chat/completions`. Confirm the configured model name
  exists on that server.
- `LLM_API_KEY` stays empty for a keyless local server. Cloud provider keys are
  server-side only and must not appear in `/bt-config.json`, browser storage,
  generated Unraid XML, or `state.json`.
- `/ping`, `/health`, and `/ready` are deliberately shallow. Use one short,
  non-sensitive translation through the authenticated public route to prove a
  real provider before translating a book. `/health/deep` is also authenticated
  and spends provider capacity.

After changing the managed environment, do not recreate a role with an ad-hoc
`docker run`; use the documented lifecycle so state and ownership remain
verifiable.

## 502 or 504 after CWA was recreated

The injection proxy resolves `CWA_UPSTREAM` when its Nginx process starts. If
CWA was recreated with a new address, restart only the managed translator proxy
and rerun `doctor`. Do not recreate CWA or the translator API for this symptom.

Also confirm CWA still joins the exact `BT_CWA_NETWORK` and that its running
image supplies the exact `BT_CWA_VERSION` tag/label expected by the plan.

## Rate-limited or slow translation

“Rate limited — waiting” means bounded admission is working. Avoid repeatedly
toggling translation or reloading, which creates more queued work. Measure the
actual local/provider latency with a short text first, then adjust only one
bounded setting at a time.

Useful controls include `BT_RATE_LIMIT_PER_MINUTE`,
`BT_CLIENT_MIN_REQUEST_GAP_MS`, `BT_MAX_UPSTREAM_INFLIGHT`, request deadline,
and output-token limits. Never set an unlimited production value. Local model,
context size, target language, and GPU memory normally dominate latency.

## Translation formatting, duplicates, or stale behavior

- Hard-refresh and confirm the Console reports the current `BT_UI_VERSION`.
- There must be only one `/bt-static/loader.js` instance. A legacy CWA overlay
  plus the managed injection proxy can load two translators; remove the legacy
  template/file mounts after preserving rollback evidence.
- Change source or target language to cancel stale work cleanly. A page turn
  intentionally discards results from the previous reader generation.
- If headings or paragraphs are missed, capture a minimal DRM-free EPUB and the
  element structure. Do not share copyrighted book content or a full private
  library database.

## XML parsing error or garbage characters

The EPUB is commonly DRM-encrypted. Check for the standard marker:

```bash
unzip -l "book.epub" | grep META-INF/encryption.xml
```

If present, CWA's web reader receives encrypted chapter bytes and cannot parse
them; the translator cannot repair or decrypt the file. Use a legally obtained
DRM-free EPUB.

## Install, state, or migration recovery

- A failed `plan` performs no mutation. Correct the named field and rerun it.
- A failed fresh install removes only newly created translator runtime
  resources and writes no successful `state.json`; CWA and user data stay
  external.
- If `state.json` alone was lost while the complete labeled split runtime is
  healthy, use `./btctl adopt --env ...`. It rejects partial or unlabeled
  resources and never converts a v2.1.4 combined container.
- For an exact v2.1.4 source, use `./btctl upgrade --env ... --yes`. If
  acceptance fails after a completed migration, use
  `./btctl rollback --env ... --yes`; never start both versions against one
  data directory.
- If the host stopped during `prepared`, `snapshot-complete`, or a re-upgrade,
  rerun the same `btctl upgrade` command. The journal verifies exact identities.
  When a complete healthy v2.2 runtime already exists, the command adopts that
  exact labeled cutover and finishes the journal without moving its live data
  bind. When no v2.2 runtime exists, it preserves incomplete work trees under
  clearly named `.preserved` paths and advances to a new numbered attempt.
  Partial or mismatched runtime resources stop recovery before any data tree is
  renamed. Do not delete preserved trees until the new runtime passes acceptance.
- For Compose permission errors, rerun with the same Docker-capable account and
  primary group used for install. Do not apply a recursive public `chmod` or
  change uid `101` manually; the managed one-shot helper restores the private
  `2750` directory and `0640` file contract before startup.
- A successful rollback may mark `target_reupgrade_status=unavailable` when the
  preserved v2.2 tree is absent or fails integrity/read checks. The legacy
  service is restored, but re-upgrade remains fail-closed until that target is
  repaired or restored from trusted evidence.
- `./btctl uninstall --env ... --yes` removes only owned runtime and preserves
  CWA, data, backups, the local image, and state evidence. It is retryable after
  an interrupted removal.

## Collecting a useful issue report

Include the exact source commit, `VERSION`, host/profile, CWA image tag,
reverse-proxy type, browser, the redacted first failed doctor check, and the
smallest relevant log window. Remove cookies, Authentik headers, public IPs,
private filesystem paths, book text, and all LLM credentials before sharing.
