# Deploying to Unraid

This document describes the **exact** architecture of the book-translator plugin
on FelixServer (Unraid at `192.168.0.122`).

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

This means updating the files in `/mnt/user/appdata/calibre-web-automated/overlay/` immediately
updates what the container serves — **no container restart needed** for frontend changes.

> ⚠️ `docker restart calibre-web-automated` does NOT pick up changes to the XML template.
> If you change the template, you must use Unraid's Docker Manager UI to apply it,
> or stop/rm/run the container manually.

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
# Should print the current version string, e.g.: const BT_UI_VERSION = '2026-06-30-opus-deploy-sync-v1';

# 4. No restart needed — file bind mounts update live.
```

---

## Update the Backend API

```bash
cd /mnt/user/appdata/book-translator-api
git pull origin main

# Rebuild the image
docker build -t local/book-translator-api:latest .

# Restart only the API container
docker restart book-translator-api

# Verify health
curl -s http://127.0.0.1:8390/health
```

---

## Initial Setup (First Deploy)

If the containers don't exist yet:

### 1. Backend container

```bash
docker run -d \
  --name book-translator-api \
  --restart unless-stopped \
  --network media-net \
  -p 8390:8390 \
  -e LLM_PROVIDER=local \
  -e LLM_MODEL=gemma4-12b \
  -e BT_LOCAL_URL=http://192.168.0.122:2819/v1/chat/completions \
  -e BT_BATCH_SIZE=5 \
  -e BT_MAX_CONCURRENT=1 \
  -e BT_TIMEOUT=120 \
  -v /mnt/user/appdata/book-translator-api/data:/app/data \
  local/book-translator-api:latest
```

> ⚠️ Do NOT use `localhost` as `BT_LOCAL_URL` inside Docker.  
> `localhost` inside a container refers to the container itself, not the host.  
> Use the host's LAN IP: `192.168.0.122`.

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
# No restart needed.
```

---

## LLM Endpoint Reference

| Backend | Correct BT_LOCAL_URL |
|---------|----------------------|
| vLLM on FelixServer | `http://192.168.0.122:2819/v1/chat/completions` |
| Ollama on FelixServer | `http://192.168.0.122:11434/v1/chat/completions` |
| LM Studio (Gaming PC) | `http://192.168.0.89:1234/v1/chat/completions` |
| ❌ Wrong | `http://localhost:2819/...` (broken inside Docker) |
