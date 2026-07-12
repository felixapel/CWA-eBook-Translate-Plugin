FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4

WORKDIR /app

# nginx powers the proxy role; gettext provides envsubst for rendering its
# config template. Direct and transitive versions are pinned so an Alpine
# repository update cannot silently change the artifact.
RUN apk add --no-cache \
    gettext=1.0-r0 \
    gettext-envsubst=1.0-r0 \
    gettext-libs=1.0-r0 \
    libgomp=15.2.0-r5 \
    libunistring=1.4.2-r0 \
    libxml2=2.13.9-r2 \
    nginx=1.30.3-r0 \
    pcre2=10.47-r1

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

# Copy only runtime modules; tests, benchmarks, and operator helpers do not
# belong in the published execution artifact.
COPY auth.py cache.py server.py singleflight.py translator.py work_budget.py ./
COPY VERSION ./
COPY static/ ./static/
COPY proxy/ ./proxy/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Keep the identity used by all previously published images. Explicit IDs
# avoid changing ownership semantics when Alpine's system users change.
RUN addgroup -S -g 102 appuser \
 && adduser -S -D -H -u 101 -G appuser appuser \
 && mkdir -p /app/data \
 && chown appuser:appuser /app/data \
 && chmod 755 /app \
 && chmod 700 /app/data

# Set environment variables for the database path and LLM configuration
ENV DB_PATH="/app/data/translations.db"
ENV BT_CACHE_TTL_DAYS="90"
ENV BT_CACHE_MAX_ENTRIES="100000"
ENV BT_CACHE_HARDEN_EXISTING_DIR="true"
ENV PORT=8390
ENV PYTHONDONTWRITEBYTECODE=1

# Provider can be: local, openai, anthropic, gemini, groq, together, minimax, deepseek, openrouter
ENV LLM_PROVIDER="local"
ENV LLM_MODEL="gemma4-12b"
# LLM_API_KEY is intentionally NOT set as an ENV. Pass it at runtime via
# `docker run -e LLM_API_KEY=...` or `--env-file`. Baking it into the
# image (even as an empty default) shows up in `docker inspect` and
# `docker history`; for a multi-user image this is a footgun.

# Stability tunables (override at runtime). For a slow local model keep
# concurrency low; BT_LOCAL_URL must point at the host, not the container.
ENV BT_MAX_CONCURRENT="2"
ENV BT_TIMEOUT="60"
# Paragraphs per LLM call — >1 is much faster on slow models (1 = legacy).
ENV BT_BATCH_SIZE="5"

# 8390 = translation API. 8080 = injection proxy. BT_ROLE selects api, proxy,
# or the backwards-compatible combined mode. EXPOSE is documentation only.
EXPOSE 8390 8080

# Probe the process selected by BT_ROLE. Combined/auto mode checks the API; its
# unprivileged monitor exits if nginx dies.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; proxy=os.environ.get('BT_ROLE')=='proxy'; p=os.environ.get('BT_PROXY_PORT','8080') if proxy else os.environ.get('PORT','8390'); path='/bt-api/ping' if proxy else '/ping'; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}{path}', timeout=4).status==200 else 1)"

# Both roles use unprivileged ports and write only to /app/data or /tmp.
USER appuser
ENTRYPOINT ["/app/docker-entrypoint.sh"]
