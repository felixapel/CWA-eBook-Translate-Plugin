# Deploying to Unraid

> **v2.0.0+: proxy-injection mode is the recommended Unraid install** and needs
> none of the bind-mounts below. Run the container with `CWA_UPSTREAM` set to
> your CWA container URL, map a free host port to container port `8080`, and
> read your library through that port — stock CWA, no template edits, survives
> CWA updates. The rest of this document describes the classic bind-mount
> deployment, kept for existing installs and development.

A concrete, worked example of a two-container deployment (translator API +
Calibre-Web-Automated with the overlay bind-mounts). The hostnames, IPs
(`192.168.0.122`), and paths below are from one real setup — substitute your
own. The general shape (API container + 3 overlay bind mounts into CWA) applies
to any Unraid/Docker host.

---

## Real Architecture

```
Unraid Host
├── /mnt/user/appdata/book-translator-api/        ← Git checkout (source of truth)
│   ├── server.py, translator.py, cache.py
│   ├── static/translator.js                      ← Build output: backend API + frontend
│   ├── static/translator.css
│   ├── overlay/read.html
│   └── data/                                     ← Mounted into container as /app/data
│
├── /mnt/user/appdata/calibre-web-automated/
│   └── overlay/                                  ← Deploy target for CWA files
│       ├── translator.js   ─── bind-mounted ──→  container: /app/.../static/js/translator.js
│       ├── translator.css  ─── bind-mounted ──→  container: /app/.../static/css/translator.css
│       └── read.html       ─── bind-mounted ──→  container: /app/.../templates/read.html
│
Containers
├── calibre-web-automated  (port 8383→8083)       ← Reads plugin files via file bind mounts
├── book-translator-api    (port 8390)            ← Translation API
└── vLLM                   (port 2819)            ← LLM backend (gemma4-12b)
```

### How the frontend is deployed

The CWA container (`crocodilestick/calibre-web-automated`) has **file-level bind mounts**
defined in its Unraid template (`/boot/config/plugins/dockerMan/templates-user/my-calibre-web-automated.xml`):

```xml
<Config Name="Plugin JS"  Target="/app/calibre-web-automated/cps/static/js/translator.js"
        Mode="ro" Type="Path">/mnt/user/appdata/calibre-web-automated/overlay/translator.js</Config>
<Config Name="Plugin CSS" Target="/app/calibre-web-automated/cps/static/css/translator.css"
        Mode="ro" Type="Path">/mnt/user/appdata/calibre-web-automated/overlay/translator.css</Config>
<Config Name="Plugin Read HTML" Target="/app/calibre-web-automated/cps/templates/read.html"
        Mode="ro" Type="Path">/mnt/user/appdata/calibre-web-automated/overlay/read.html</Config>
```

Because these are bind mounts (not files baked into the image), editing them on the host
changes what the container sees on disk immediately — but that's not the whole story for
whether the *running app* picks it up without a restart:

- **`translator.js` / `translator.css`** — Flask serves static files fresh from disk on
  every request (no app-level caching), so these are typically picked up live.
- **`read.html`** — this is a Jinja2 **template**, not a static file. Flask/Jinja2 caches
  compiled templates in memory after first render, and CWA does not set
  `TEMPLATES_AUTO_RELOAD` / run in debug mode, so a change to `read.html` (e.g. bumping
  the cache-busting version, editing `window.BOOK_TRANSLATOR`) may **not** take effect
  until the process restarts.

**Restart `calibre-web-automated` after any overlay change to be safe** — that's the
practice used throughout this project's actual deploys. Skipping the restart for a
JS/CSS-only change *might* work, but isn't guaranteed and isn't worth the ambiguity.

> ⚠️ `docker restart calibre-web-automated` does NOT pick up changes to the XML template
> itself (the bind-mount definitions). If you change *which paths* are mounted, you must
> use Unraid's Docker Manager UI to apply the template, or stop/rm/run the container
> manually — a plain restart only re-runs the existing container with its existing mounts.

---

## Update the Frontend

```bash
# 1. Pull latest from Gitea
cd /mnt/user/appdata/book-translator-api
git pull origin main

# 2. Copy updated files to the CWA overlay
cp static/translator.js  /mnt/user/appdata/calibre-web-automated/overlay/translator.js
cp static/translator.css /mnt/user/appdata/calibre-web-automated/overlay/translator.css
cp overlay/read.html     /mnt/user/appdata/calibre-web-automated/overlay/read.html

# 3. Verify version marker
grep "BT_UI_VERSION" /mnt/user/appdata/calibre-web-automated/overlay/translator.js
# Should print the current version string — see the latest entry in CHANGELOG.md.

# 4. Restart so the read.html template (Jinja2, may be cached in memory) is
#    definitely re-rendered with the new cache-busting version / config:
docker restart calibre-web-automated
```

---

## Update the Backend API

> ⚠️ `docker restart` does **not** pick up a rebuilt image — it re-runs the
> existing container with the image it was created from. After rebuilding you
> must **recreate** the container. In the Unraid UI this is just editing the
> container and clicking *Apply*. From the shell:

```bash
cd /mnt/user/appdata/book-translator-api
git pull origin main

# Rebuild the image
docker build -t local/book-translator-api:latest .

# Recreate the container so it runs the NEW image (the /app/data bind mount
# keeps the SQLite cache). Re-use your exact env — see "Initial Setup" below,
# or copy the flags from `docker inspect book-translator-api` first.
docker rm -f book-translator-api
docker run -d --name book-translator-api --restart unless-stopped --net bridge \
  -p 8390:8390 -v /mnt/user/appdata/book-translator-api/data:/app/data \
  -l net.unraid.docker.managed=dockerman \
  -e LLM_PROVIDER=local -e LLM_MODEL=gemma4-12b \
  -e BT_LOCAL_URL=http://<YOUR-HOST-IP>:2819/v1/chat/completions \
  -e BT_BATCH_SIZE=3 -e BT_MAX_CONCURRENT=1 -e BT_TIMEOUT=60 \
  -e BT_CONTEXT_WINDOW=1 -e BT_MAX_TOKENS=640 -e BT_BATCH_MAX_TOKENS=1200 \
  local/book-translator-api:latest

# Verify (ping = fast liveness, health = deep probe incl. the LLM)
curl -s http://127.0.0.1:8390/ping
curl -s http://127.0.0.1:8390/health
```

---

## Initial Setup (First Deploy)

If the containers don't exist yet:

### 1. Backend container

**Recommended: run it as an Unraid-managed container.** The image is built
locally (`local/book-translator-api:latest`) and isn't in a registry, so the
cleanest path is:

1. Run `install_unraid.sh` from a clone of the repo — it builds the image and
   installs the Unraid template (`my-book-translator-api.xml`) into
   `/boot/config/plugins/dockerMan/templates-user/`.
2. In the Unraid **Docker** tab → *Add Container* → pick `book-translator-api`
   from the Template dropdown → set your `BT_LOCAL_URL` → **Apply**.

Applying via the UI gives the container the `net.unraid.docker.managed=dockerman`
label and an autostart entry, so Unraid treats it as a first-class managed
container (it won't be seen as an "orphan" and removed, and it starts with the
array).

**Manual `docker run` alternative.** If you create it by hand, include the
management label and autostart it yourself, otherwise Unraid may treat it as an
orphan:

```bash
docker run -d \
  --name book-translator-api \
  --restart unless-stopped \
  --net bridge \
  -p 8390:8390 \
  -e LLM_PROVIDER=local \
  -e LLM_MODEL=gemma4-12b \
  -e BT_LOCAL_URL=http://<YOUR-HOST-IP>:2819/v1/chat/completions \
  -e BT_BATCH_SIZE=3 \
  -e BT_MAX_CONCURRENT=1 \
  -e BT_TIMEOUT=60 \
  -e BT_CONTEXT_WINDOW=1 \
  -e BT_MAX_TOKENS=640 \
  -e BT_BATCH_MAX_TOKENS=1200 \
  -v /mnt/user/appdata/book-translator-api/data:/app/data \
  -l net.unraid.docker.managed=dockerman \
  local/book-translator-api:latest

# Make it start with the array (Unraid autostart is a plain name list):
grep -qxF book-translator-api /var/lib/docker/unraid-autostart \
  || echo book-translator-api >> /var/lib/docker/unraid-autostart
```

> ⚠️ Do NOT use `localhost` as `BT_LOCAL_URL` inside Docker.
> `localhost` inside a container refers to the container itself, not the host.
> Use the host's LAN IP (e.g. `192.168.0.122`) or `host.docker.internal`.

> Note: `local/book-translator-api:latest` is built locally, not pulled from a
> registry. It persists across reboots, but if you ever recreate the Docker
> image (`docker.img`) you must rebuild it: `cd /mnt/user/appdata/book-translator-api
> && docker build -t local/book-translator-api:latest .`

### 2. CWA container recreation with plugin mounts

If the CWA container is already running but lacks the file bind mounts:

```bash
docker stop calibre-web-automated && docker rm calibre-web-automated

docker run -d \
  --name calibre-web-automated \
  --restart unless-stopped \
  --network media-net \
  -p 8383:8083 \
  -e PUID=99 -e PGID=100 -e TZ=UTC \
  -v "/mnt/user/MEDIA/Books/Calibre Library:/calibre-library:rw" \
  -v "/mnt/user/appdata/calibre-web-automated:/config:rw" \
  -v "/mnt/user/downloads/completed/cwa-book-ingest:/cwa-book-ingest:rw" \
  -v "/mnt/user/appdata/calibre-web-automated/overlay/read.html:/app/calibre-web-automated/cps/templates/read.html:ro" \
  -v "/mnt/user/appdata/calibre-web-automated/overlay/translator.js:/app/calibre-web-automated/cps/static/js/translator.js:ro" \
  -v "/mnt/user/appdata/calibre-web-automated/overlay/translator.css:/app/calibre-web-automated/cps/static/css/translator.css:ro" \
  crocodilestick/calibre-web-automated:latest
```

---

## Verify Deployment

```bash
# Version in overlay
grep -n "BT_UI_VERSION" /mnt/user/appdata/calibre-web-automated/overlay/translator.js

# Cache-busting in read.html
grep -n "?v=" /mnt/user/appdata/calibre-web-automated/overlay/read.html

# Version in container (must match overlay)
docker exec calibre-web-automated grep -n "BT_UI_VERSION" /app/calibre-web-automated/cps/static/js/translator.js

# Hashes must match
sha256sum /mnt/user/appdata/calibre-web-automated/overlay/translator.js
docker exec calibre-web-automated sha256sum /app/calibre-web-automated/cps/static/js/translator.js

# Backend health
curl -s http://127.0.0.1:8390/health
```

---

## Rollback

Backups of previous overlay files are in:
```
/mnt/user/appdata/book-translator-api/backups/<YYYYMMDD-HHMMSS>/cwa-overlay/
```

To roll back:
```bash
BACKUP=/mnt/user/appdata/book-translator-api/backups/<timestamp>/cwa-overlay
cp $BACKUP/translator.js  /mnt/user/appdata/calibre-web-automated/overlay/translator.js
cp $BACKUP/translator.css /mnt/user/appdata/calibre-web-automated/overlay/translator.css
cp $BACKUP/read.html      /mnt/user/appdata/calibre-web-automated/overlay/read.html
docker restart calibre-web-automated
```

---

## LLM Endpoint Reference

| Backend | Correct BT_LOCAL_URL |
|---------|----------------------|
| vLLM on FelixServer | `http://192.168.0.122:2819/v1/chat/completions` |
| Ollama on FelixServer | `http://192.168.0.122:11434/v1/chat/completions` |
| LM Studio (Gaming PC) | `http://192.168.0.89:1234/v1/chat/completions` |
| ❌ Wrong | `http://localhost:2819/...` (broken inside Docker) |
