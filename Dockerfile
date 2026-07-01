FROM python:3.11-alpine

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY *.py ./

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

# Expose the default port. PORT is honored at runtime (gunicorn bind + healthcheck
# below); if you remap it, also remap the -p flag / docker-compose port accordingly —
# EXPOSE itself is documentation only, Docker can't make it dynamic.
EXPOSE 8390

# Liveness probe (Python only; no curl in the slim alpine image). Reads $PORT so a
# remapped port is still probed correctly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8390'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/ping', timeout=4).status==200 else 1)"

# Command to run gunicorn (1 worker so the in-memory rate-limit/metrics/cache stay
# coherent — see README "Why a single worker"). Shell form so $PORT expands; `exec`
# is required so gunicorn replaces the shell as PID 1 and receives `docker stop`'s
# SIGTERM directly, instead of the shell swallowing it and Docker having to wait out
# the full grace period and SIGKILL. Verified: `docker stop` on this image returns
# in ~1s. Docker's linter flags shell-form CMD generically — that warning is for the
# no-`exec` case and doesn't apply here.
CMD sh -c 'exec gunicorn --bind 0.0.0.0:${PORT:-8390} --workers 1 --threads 8 --timeout 120 server:app'
