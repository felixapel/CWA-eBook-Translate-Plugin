# Community Applications install on Unraid

This is the simplest supported v2.2.1 path. It targets Unraid 7.3.2 on
linux/amd64 (x86_64), an existing stock CWA 4.0.6 container, native CWA session
authentication, and a local OpenAI-compatible LLM. Use
[DEPLOY_UNRAID.md](DEPLOY_UNRAID.md) and `btctl` instead when you need split
roles, upgrade/rollback, Authentik-forwarded identity, or a topology outside
this exact profile.

## Before opening Community Applications

Know the CWA container name, a Docker network shared with it, the exact public
reader origin, and an LLM URL reachable from Docker. CWA strong sessions assume
the default `TRUSTED_PROXY_COUNT=1`; custom hop counts are not certified.

Create the only writable bind with the image's stable identity:

```bash
install -d -m 0700 -o 101 -g 102 /mnt/user/appdata/cwa-translate-ca/data
```

The directory must remain private mode 0700 and owned by 101:102. The long-lived
container does not run as root and will fail startup instead of repairing an
unsafe or unwritable directory.

## Install and configure

Install **CWA eBook Translate** from Community Applications. The approved
template must use the exact digest recorded for v2.2.1; do not replace it with
a mutable image reference. If the installed DockerMan version rejects digest
syntax, use only the documented immutable `2.2.1` fallback and verify the image
ID against the release record. Never use a moving image tag for an exact
install.

Configure:

- CWA upstream: `http://<your-cwa-container>:8083`
- CWA container network: the existing network selected in the template
- Public origin: the exact browser-facing origin, such as
  `https://books.example.com`
- Auth mode: `cwa_session`
- CWA auth URL: `http://<your-cwa-container>:8083/ajax/emailstat`
- LLM provider/model and absolute `/v1/chat/completions` URL
- App data: `/mnt/user/appdata/cwa-translate-ca/data` to `/app/data`

The template explicitly sets `BT_ROLE=all`. Container port 8080 is the only
published service and defaults to host port 8385. Internal API port 8390 must
not be published. Route the browser/reverse proxy through 8385; keep OPDS/Kobo
routes pointed directly at CWA.

## Runtime security contract

The template must retain all of these settings:

- user `101:102`;
- read-only root filesystem;
- `/tmp` tmpfs owned by 101:102 with `noexec,nosuid`;
- `--cap-drop=ALL` and `--security-opt=no-new-privileges:true`;
- private `/app/data` bind;
- native CWA-session validation, never disabled auth or a browser token.

## Acceptance

After install:

1. Open CWA through the translator port/domain and sign in again.
2. Confirm `/bt-api/ping` returns `200` and `/bt-api/metrics` returns `401`
   without the current CWA session.
3. Open one DRM-free EPUB, select languages, and translate two paragraphs.
4. Recreate only the translator container and verify the same text is served
   from the persistent cache.
5. Confirm Unraid shows no host mapping for container port 8390 and the image
   reference resolves to the exact release digest.

Stop and use [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if authentication is
disabled, 8390 is published, ownership differs, the image is mutable, or a
check above fails.
