FROM python:3.11-alpine

WORKDIR /app

# nginx powers the optional proxy-injection mode (enabled by setting
# CWA_UPSTREAM); gettext provides envsubst for rendering its config template.
RUN apk add --no-cache nginx gettext

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and runtime assets
COPY *.py ./
COPY VERSION ./
COPY static/ ./static/
COPY proxy/ ./proxy/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Create a volume for the sqlite database
VOLUME ["/app/data"]

# Set environment variables for the database path and LLM configuration
ENV DB_PATH="/app/data/translations.db"
ENV PORT=8390

# Provider can be: local, openai, anthropic, gemini, groq, together, minimax, deepseek, openrouter
ENV LLM_PROVIDER="local"
ENV LLM_MODEL="gemma4-12b"
ENV LLM_API_KEY=""

# Stability tunables (override at runtime). For a slow local model keep
# concurrency low; BT_LOCAL_URL must point at the host, not the container.
ENV BT_MAX_CONCURRENT="2"
ENV BT_TIMEOUT="60"
# Paragraphs per LLM call — >1 is much faster on slow models (1 = legacy).
ENV BT_BATCH_SIZE="5"

# 8390 = translation API (always on). 8080 = injection proxy, active only when
# CWA_UPSTREAM is set (read CWA through it and the overlay appears with zero
# changes to the CWA container). PORT/BT_PROXY_PORT are honored at runtime;
# EXPOSE itself is documentation only.
EXPOSE 8390 8080

# Liveness probe (Python only; no curl in the slim alpine image). Reads $PORT so a
# remapped port is still probed correctly. Probes the API — the entrypoint's
# monitor loop already exits the container if nginx dies in proxy mode.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8390'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/ping', timeout=4).status==200 else 1)"

# The entrypoint runs gunicorn (1 worker so the in-memory rate-limit/metrics
# stay coherent — see README "Why a single worker") and, in proxy mode, nginx.
# It forwards SIGTERM to both and exits if either dies, so `docker stop` stays
# fast and the restart policy can recover a half-dead container.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
