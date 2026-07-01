"""
book-translator — Flask microservice for ebook paragraph translation.
Runs on port 8390. Frontend (CWA overlay) calls this service.
"""
import logging
import os
import re
import time
import threading
import uuid
from collections import defaultdict
from flask import Flask, request, jsonify

from translator import translate_text, translate_batch, check_backend_health, LLM_MODEL
from cache import get_cached, put_cache, get_cache_stats, cleanup_old_entries

# Optional shared-secret. When BT_API_TOKEN is set, translate endpoints require
# the matching `X-BT-Token` header — use it if the API is reachable beyond the LAN.
API_TOKEN = os.environ.get("BT_API_TOKEN", "")

# ── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("book-translator.server")

app = Flask(__name__)

# ── Language validation (H7) ────────────────────────────────────────────────

VALID_LANGUAGES = {
    "Arabic", "Bulgarian", "Chinese", "Czech", "Danish", "Dutch", "English",
    "Estonian", "Finnish", "French", "German", "Greek", "Hebrew", "Hindi",
    "Hungarian", "Indonesian", "Italian", "Japanese", "Korean", "Latvian",
    "Lithuanian", "Norwegian", "Polish", "Portuguese", "Romanian", "Russian",
    "Slovak", "Slovenian", "Spanish", "Swedish", "Thai", "Turkish",
    "Ukrainian", "Vietnamese",
}


def _validate_languages(source_lang: str, target_lang: str):
    """Return error string if languages are invalid, else None."""
    invalid = []
    if source_lang not in VALID_LANGUAGES:
        invalid.append(f"source_lang '{source_lang}'")
    if target_lang not in VALID_LANGUAGES:
        invalid.append(f"target_lang '{target_lang}'")
    if invalid:
        return f"Invalid language(s): {', '.join(invalid)}. Valid: {sorted(VALID_LANGUAGES)}"
    return None


# ── CORS whitelist (H5) ─────────────────────────────────────────────────────

ALLOWED_ORIGINS = {
    "http://localhost:8383",
    "http://localhost:8083",
    "https://calibre.felitounraid.de",
}
_LOCAL_ORIGIN_RE = re.compile(r"^https?://192\.168\.0\.\d{1,3}(:\d+)?$")


def _is_origin_allowed(origin: str | None) -> str | None:
    """Return the origin if it's allowed, else None."""
    if not origin:
        return None
    if origin in ALLOWED_ORIGINS:
        return origin
    if _LOCAL_ORIGIN_RE.match(origin):
        return origin
    return None


# ── Rate limiter (H6) ───────────────────────────────────────────────────────

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
BT_RATE_LIMIT_PER_MINUTE = int(os.environ.get("BT_RATE_LIMIT_PER_MINUTE", "120"))
BT_RATE_LIMIT_RETRY_AFTER = int(os.environ.get("BT_RATE_LIMIT_RETRY_AFTER", "10"))

RATE_LIMIT_MAX = BT_RATE_LIMIT_PER_MINUTE
RATE_LIMIT_WINDOW = 60


def _cleanup_rate_limits():
    """Background thread to clean up inactive IPs from the rate limiter."""
    while True:
        time.sleep(3600)  # Every hour
        now = time.monotonic()
        cutoff = now - RATE_LIMIT_WINDOW
        with _rate_limit_lock:
            keys_to_delete = []
            for ip, timestamps in _rate_limit_store.items():
                active = [t for t in timestamps if t > cutoff]
                if not active:
                    keys_to_delete.append(ip)
                else:
                    _rate_limit_store[ip] = active
            for ip in keys_to_delete:
                del _rate_limit_store[ip]

threading.Thread(target=_cleanup_rate_limits, daemon=True).start()

def _check_rate_limit(ip: str) -> bool:
    """Return True if the request should be allowed, False if rate-limited."""
    now = time.monotonic()
    with _rate_limit_lock:
        timestamps = _rate_limit_store[ip]
        # Evict expired timestamps
        cutoff = now - RATE_LIMIT_WINDOW
        _rate_limit_store[ip] = [t for t in timestamps if t > cutoff]
        if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
            return False
        _rate_limit_store[ip].append(now)
        return True


# ── Metrics (M5) ────────────────────────────────────────────────────────────

_metrics_lock = threading.Lock()
_metrics = {
    "total_requests": 0,
    "total_latency_ms": 0.0,
    "cache_hits": 0,
    "cache_misses": 0,
    "errors": 0,
}


def _record_metric(latency_ms: float, hits: int = 0, misses: int = 0, error: bool = False):
    """Record request metrics (thread-safe)."""
    with _metrics_lock:
        _metrics["total_requests"] += 1
        _metrics["total_latency_ms"] += latency_ms
        _metrics["cache_hits"] += hits
        _metrics["cache_misses"] += misses
        if error:
            _metrics["errors"] += 1


# ── Shared batch helper (M3) ───────────────────────────────────────────────

def _translate_paragraphs(
    paragraphs: list[str], source_lang: str, target_lang: str
) -> dict:
    """
    Shared helper for batch translation logic used by /translate/batch.

    Returns dict with translations list, cached_count, fresh_count, and elapsed_ms.
    """
    translations = [""] * len(paragraphs)
    cached_count = 0
    fresh_count = 0
    start = time.monotonic()

    # Identify cache misses
    misses = []
    miss_indices = []
    
    for i, para in enumerate(paragraphs):
        if not para.strip():
            continue
        cached = get_cached(para, source_lang, target_lang)
        if cached is not None:
            translations[i] = cached
            cached_count += 1
        else:
            misses.append(para)
            miss_indices.append(i)

    # Translate misses concurrently (concurrency controlled by BT_MAX_CONCURRENT)
    if misses:
        results = translate_batch(misses, source_lang, target_lang)
        for idx, (translated, backend) in zip(miss_indices, results):
            translations[idx] = translated
            if not translated.startswith("[TRANSLATION ERROR:"):
                fresh_count += 1
                try:
                    put_cache(paragraphs[idx], source_lang, target_lang, translated, model=LLM_MODEL)
                except Exception:
                    pass

    total_elapsed_ms = int((time.monotonic() - start) * 1000)

    return {
        "translations": translations,
        "cached_count": cached_count,
        "fresh_count": fresh_count,
        "total_elapsed_ms": total_elapsed_ms,
    }


# ── Request middleware (M5: request ID + timing, H6: rate limiting) ─────────

@app.before_request
def before_request_hook():
    """Attach request ID, check rate limit."""
    request.request_id = str(uuid.uuid4())
    request.start_time = time.monotonic()

    # Optional shared-secret auth (skip preflight + health so they always work).
    if API_TOKEN and request.method != "OPTIONS" and request.path not in ("/health", "/ping"):
        if request.headers.get("X-BT-Token") != API_TOKEN:
            return jsonify({"error": "Unauthorized", "request_id": request.request_id}), 401

    # Rate limiting (H6) — skip for health/metrics/ping
    if request.path not in ("/health", "/metrics", "/ping"):
        client_ip = request.remote_addr or "unknown"
        if not _check_rate_limit(client_ip):
            log.warning("Rate limit exceeded for %s (req %s)", client_ip, request.request_id)
            response = jsonify({
                "error": "rate_limited",
                "retry_after": BT_RATE_LIMIT_RETRY_AFTER,
                "request_id": request.request_id,
            })
            response.headers["Retry-After"] = str(BT_RATE_LIMIT_RETRY_AFTER)
            return response, 429


@app.after_request
def after_request_hook(response):
    """Log timing, add CORS headers, add request ID header."""
    # Timing (M5)
    elapsed_ms = int((time.monotonic() - getattr(request, "start_time", time.monotonic())) * 1000)
    req_id = getattr(request, "request_id", "unknown")
    log.info(
        "req=%s method=%s path=%s status=%d elapsed=%dms",
        req_id, request.method, request.path, response.status_code, elapsed_ms,
    )
    response.headers["X-Request-ID"] = req_id

    # CORS (H5: origin whitelist)
    origin = request.headers.get("Origin")
    allowed = _is_origin_allowed(origin)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = allowed
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-BT-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return response


# ── Routes ──────────────────────────────────────────────────────────────────


@app.route("/ping")
def ping():
    """Liveness probe — instant, never touches the LLM. Used by the Docker
    HEALTHCHECK so a busy/slow vLLM can't mark the container unhealthy while it
    is in fact serving translations. /health (below) remains the deep probe."""
    return jsonify({"status": "ok"})


@app.route("/health")
def health():
    """Health check with backend status (M2)."""
    backend_health = check_backend_health()
    overall = "ok" if any(
        b.get("status") == "ok" for b in backend_health.values()
    ) else "degraded"
    return jsonify({
        "status": overall,
        "service": "book-translator",
        "backends": backend_health,
        "request_id": getattr(request, "request_id", None),
    })


@app.route("/stats")
def stats():
    """Return cache statistics."""
    return jsonify(get_cache_stats())


@app.route("/metrics")
def metrics():
    """Return request metrics (M5)."""
    with _metrics_lock:
        snapshot = dict(_metrics)
    total = snapshot["total_requests"]
    avg_latency = round(snapshot["total_latency_ms"] / total, 1) if total > 0 else 0
    total_cache = snapshot["cache_hits"] + snapshot["cache_misses"]
    cache_hit_rate = round(snapshot["cache_hits"] / total_cache * 100, 1) if total_cache > 0 else 0
    return jsonify({
        "total_requests": total,
        "average_latency_ms": avg_latency,
        "cache_hit_rate_pct": cache_hit_rate,
        "cache_hits": snapshot["cache_hits"],
        "cache_misses": snapshot["cache_misses"],
        "errors": snapshot["errors"],
    })


@app.route("/translate", methods=["POST"])
def translate():
    """
    Translate a single paragraph.

    POST body: {
        "text": "Hello world",
        "source_lang": "English",
        "target_lang": "Spanish"
    }

    Returns: {
        "translated": "Hola mundo",
        "cached": true/false,
        "elapsed_ms": 1234,
        "backend": "local"|"minimax",
        "request_id": "uuid"
    }
    """
    data = request.get_json(silent=True) or {}
    if "text" not in data or not isinstance(data["text"], str):
        return jsonify({"error": "Missing or invalid 'text' field"}), 400

    text = data["text"].strip()
    source_lang = data.get("source_lang", "English")
    target_lang = data.get("target_lang", "Spanish")

    # Validate languages (H7)
    lang_error = _validate_languages(source_lang, target_lang)
    if lang_error:
        return jsonify({"error": lang_error}), 400

    req_id = getattr(request, "request_id", None)

    if not text:
        _record_metric(0, hits=0, misses=1)
        return jsonify({"translated": "", "cached": False, "elapsed_ms": 0, "request_id": req_id})

    # Check cache first
    cached = get_cached(text, source_lang, target_lang)
    if cached is not None:
        _record_metric(0, hits=1, misses=0)
        return jsonify({
            "translated": cached,
            "cached": True,
            "elapsed_ms": 0,
            "request_id": req_id,
        })

    # Translate via best available backend
    start = time.monotonic()
    try:
        translated, backend = translate_text(text, source_lang, target_lang)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _record_metric(elapsed_ms, hits=0, misses=1, error=True)
        log.exception("Translation failed")
        return jsonify({"error": str(e), "request_id": req_id}), 500

    elapsed_ms = int((time.monotonic() - start) * 1000)
    _record_metric(elapsed_ms, hits=0, misses=1)

    # Store in cache with correct model name
    try:
        put_cache(text, source_lang, target_lang, translated, model=LLM_MODEL)
    except Exception as e:
        log.exception("Cache write failed (non-fatal)")

    return jsonify({
        "translated": translated,
        "cached": False,
        "elapsed_ms": elapsed_ms,
        "backend": backend,
        "request_id": req_id,
    })


@app.route("/translate/batch", methods=["POST"])
def translate_batch_endpoint():
    """
    Translate multiple paragraphs. Used for pre-fetching next pages.

    POST body: {
        "paragraphs": ["Paragraph 1", "Paragraph 2", ...],
        "source_lang": "English",
        "target_lang": "Spanish"
    }

    Returns: {
        "translations": ["Translated 1", "Translated 2", ...],
        "cached_count": N,
        "fresh_count": M,
        "total_elapsed_ms": 12345,
        "request_id": "uuid"
    }
    """
    data = request.get_json(silent=True) or {}
    if "paragraphs" not in data or not isinstance(data["paragraphs"], list):
        return jsonify({"error": "Missing or invalid 'paragraphs' field"}), 400

    paragraphs = data["paragraphs"]
    source_lang = data.get("source_lang", "English")
    target_lang = data.get("target_lang", "Spanish")

    # Validate languages (H7)
    lang_error = _validate_languages(source_lang, target_lang)
    if lang_error:
        return jsonify({"error": lang_error}), 400

    result = _translate_paragraphs(paragraphs, source_lang, target_lang)
    result["request_id"] = getattr(request, "request_id", None)

    _record_metric(result["total_elapsed_ms"], hits=result["cached_count"], misses=result["fresh_count"])

    return jsonify(result)




@app.route("/cache/cleanup", methods=["POST"])
def cache_cleanup():
    """Evict old cache entries. Optional body: {"days": 30}"""
    data = request.get_json(silent=True) or {}
    days = data.get("days", 30)
    deleted = cleanup_old_entries(days=days)
    return jsonify({"deleted": deleted, "days": days})


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8390"))
    log.info("Starting book-translator on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
