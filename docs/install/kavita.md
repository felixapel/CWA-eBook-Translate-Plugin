# Kavita managed installation

Kavita support uses the same `btctl` split deployment as CWA: one stock reader,
one injection proxy and one private translation API. It does not fork Kavita,
mount files into its container or write translated text back to the library.

This integration is deliberately narrow. The only accepted target is stock
[Kavita v0.9.0.2](https://github.com/Kareadita/Kavita/releases/tag/v0.9.0.2),
corresponding to commit
[`6bcd5689385d0e96824982d843c54f15ce784ddc`](https://github.com/Kareadita/Kavita/commit/6bcd5689385d0e96824982d843c54f15ce784ddc).
Only the web EPUB route
`/library/<libraryId>/series/<seriesId>/book/<chapterId>` and its
`.book-content` DOM are active. Manga, PDF, OPDS, mobile apps, writeback and
other Kavita versions are outside the accepted contract.

## Isolate it from CWA

A CWA and a Kavita connector may use the same source checkout and image, but
they must be separate installations. Give each one a distinct install name,
public port/origin, state directory, data directory and backup directory. Never
point both API roles at one SQLite directory or one `reader_session_key`.

```text
browser -> kavita-translate-proxy -> stock Kavita :5000
                    |
                    +-> kavita-translate-api -> LLM
                                      |
                                      +-> private Kavita translation data
```

Keep the existing CWA environment file unchanged. Copy [`.env.example`](../../.env.example)
to a second private file and replace its CWA reader block with:

```dotenv
BT_INSTALL_NAME=kavita-translate
BT_AUTH_PROFILE=reader-session
BT_PUBLIC_ORIGIN=https://kavita-books.example.com

BT_READER_TYPE=kavita
BT_READER_UPSTREAM=http://kavita:5000
BT_READER_CONTAINER=kavita
BT_READER_NETWORK=kavita_default
BT_READER_VERSION=0.9.0.2
BT_CWA_IDENTITY_HEADER=

BT_STATE_DIR=/mnt/user/appdata/kavita-translate/state
BT_DATA_DIR=/mnt/user/appdata/kavita-translate/data
BT_BACKUP_DIR=/mnt/user/backups/kavita-translate
```

Use the real container and network names. `BT_READER_UPSTREAM` must be exactly
`http://<BT_READER_CONTAINER>:5000`. HTTPS is mandatory for a non-loopback
`BT_PUBLIC_ORIGIN`; the session cookie uses the `__Host-` boundary.

Then use the normal lifecycle:

```bash
./btctl plan --env /absolute/private/path/kavita-translate.env
./btctl install --env /absolute/private/path/kavita-translate.env --yes
./btctl doctor --env /absolute/private/path/kavita-translate.env
```

Review `plan` before installing. It must identify `reader_type` as `kavita`,
the exact version as `0.9.0.2`, and two resources named from
`kavita-translate`. `doctor` must report every check as `ok`.

## Authentication boundary

Sign in to Kavita through `BT_PUBLIC_ORIGIN`, then open a supported EPUB. The
loader submits either Kavita's native access token or its exact OIDC session
cookie only to `POST /bt-api/session`. The API validates that proof against
Kavita's `/api/Account`, discards it and issues an opaque, `HttpOnly`,
`SameSite=Strict` translator cookie for at most five minutes. Ordinary
translation routes strip the Kavita bearer token and cookies and accept only
that plugin cookie. The opaque session is also bound to the proxy-observed
client address and browser User-Agent.

The native refresh token is never read. No Kavita credential is persisted in
SQLite, lifecycle state, generated environment files or browser translator
configuration. OIDC deployments must keep the stock account endpoint reachable
at the configured internal Kavita upstream; custom authentication plugins are
not certified.

## Browser acceptance

Before relying on the connector:

1. Confirm stock Kavita reports exactly `0.9.0.2` and `doctor` is fully green.
2. Sign in through the configured HTTPS origin and open a DRM-free EPUB at the
   exact `/library/.../series/.../book/...` route.
3. Translate one non-sensitive paragraph, change language/display mode, move
   between chapters and reload the page.
4. Navigate to a manga, PDF or non-reader page and confirm no translation
   toolbar is mounted and no translation request is sent.
5. Inspect browser storage and generated files: neither a provider key nor a
   translator token may appear. Kavita's own native login state is unchanged.
6. Confirm a separate CWA installation, if present, still has its own names,
   network attachment, data and lifecycle state.

Automated Chromium and real-container gates cover the route, DOM, native-token
exchange, credential stripping and SPA teardown. Physical stock-Unraid and
real-Kavita browser acceptance is still required before the Kavita profile is
promoted from candidate to stable support. Community Applications does not
install this profile; use `btctl`.
