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

# Single version source: the VERSION file (also stamped into cache-bust query
# strings by the proxy). Falls back to "dev" for odd working directories.
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as _vf:
        __version__ = _vf.read().strip() or "dev"
except OSError:
    __version__ = "dev"

# Optional shared-secret. When BT_API_TOKEN is set, translate endpoints require
# the matching `X-BT-Token` header — use it if the API is reachable beyond the LAN.
API_TOKEN = os.environ.get("BT_API_TOKEN", "")

# Request-size caps: one request must not be able to trigger unbounded LLM work
# (GPU starvation locally, an open-ended bill on cloud APIs). Oversized input is
# rejected with 413 rather than truncated — silent truncation would corrupt text.
BT_MAX_BATCH_PARAGRAPHS = int(os.environ.get("BT_MAX_BATCH_PARAGRAPHS", "50"))
BT_MAX_PARAGRAPH_CHARS = int(os.environ.get("BT_MAX_PARAGRAPH_CHARS", "8000"))

# Global request-size cap (defence in depth). The per-field caps above check
# the *parsed* content; MAX_CONTENT_LENGTH is a hard backstop at the WSGI
# layer that rejects a 10 MB JSON before we even start parsing. With our
# defaults (50 paragraphs × 8000 chars + overhead) the per-request ceiling is
# ~400 KB; the 2 MB default here gives ~5× headroom for a single oversized
# paragraph and rejects a 10 MB body long before the per-field check fires.
# Operators behind a slow link can lower it; operators on a research cluster
# translating longer paragraphs can raise it (or lower BT_MAX_PARAGRAPH_CHARS).
BT_MAX_CONTENT_LENGTH = int(os.environ.get("BT_MAX_CONTENT_LENGTH", str(2 * 1024 * 1024)))

# Rate-limit key: request.remote_addr by default. Behind a reverse proxy every
# client shares the proxy's address, so opt in to X-Forwarded-For ONLY when
# the proxy is trusted to set it (never trust it from direct clients).
#
# BT_TRUST_PROXY is a boolean switch: when true, X-Forwarded-For's first hop
# becomes the rate-limit key. The safer BT_TRUSTED_PROXIES is a comma-
# separated list of CIDRs/ips that remote_addr must match before X-Forwarded-
# -For is honored. Set BT_TRUSTED_PROXIES (e.g. "127.0.0.1/32,::1/128" for
# a local nginx, or "10.0.0.0/8" for a private-network reverse proxy) to
# prevent spoofing: a client on the LAN can otherwise send an arbitrary
# X-Forwarded-For header and bypass the rate limiter per request.
#
# Precedence:
#   - BT_TRUSTED_PROXIES is set  -> honor X-Forwarded-For IFF remote_addr is
#                                   in the list
#   - BT_TRUST_PROXY=true         -> honor X-Forwarded-For from any peer
#                                   (legacy / dev only; not safe in prod)
#   - otherwise                   -> use remote_addr
BT_TRUST_PROXY = os.environ.get("BT_TRUST_PROXY", "false").lower() in ("1", "true", "yes")
BT_TRUSTED_PROXIES = {
    p.strip() for p in os.environ.get("BT_TRUSTED_PROXIES", "").split(",") if p.strip()
}
import ipaddress  # local import: used only when BT_TRUSTED_PROXIES is set
_TRUSTED_PROXY_NETS = [ipaddress.ip_network(c, strict=False) for c in BT_TRUSTED_PROXIES]

# ── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("book-translator.server")

app = Flask(__name__)

# Reject oversize request bodies at the WSGI layer (defence in depth — the
# per-field caps in the route handlers are the second backstop). Returning
# 413 here means a 10 MB JSON gets rejected before Flask even parses it.
app.config["MAX_CONTENT_LENGTH"] = BT_MAX_CONTENT_LENGTH

# ── Language validation (H7) ────────────────────────────────────────────────
# The selectable set mirrors Gemma 4's pre-training coverage (top-10 most
# spoken + Gemma's benchmarked and wider language groups). Must stay in sync
# with TOP_LANGUAGES/MORE_LANGUAGES in static/translator.js (test-enforced).

VALID_LANGUAGES = {
    "Afrikaans", "Albanian", "Amharic", "Arabic", "Aymara", "Basque",
    "Bengali", "Bosnian", "Bulgarian", "Burmese", "Catalan", "Cebuano",
    "Chewa", "Chinese", "Chinese (Traditional)", "Croatian", "Czech",
    "Danish", "Dutch", "English", "Esperanto", "Estonian", "Finnish",
    "French", "Gaelic", "Galician", "Ganda", "German", "Greek", "Guarani",
    "Gujarati", "Hausa", "Hawaiian", "Hebrew", "Hindi", "Hungarian",
    "Icelandic", "Igbo", "Indonesian", "Italian", "Japanese", "Javanese",
    "Kannada", "Kazakh", "Khmer", "Korean", "Kyrgyz", "Lao", "Latin",
    "Latvian", "Lingala", "Lithuanian", "Macedonian", "Maithili",
    "Malagasy", "Malay", "Malayalam", "Maori", "Marathi", "Mongolian",
    "Nahuatl", "Navajo", "Nepali", "Norwegian", "Odia", "Oromo", "Pashto",
    "Persian", "Polish", "Portuguese", "Punjabi", "Quechua", "Romanian",
    "Russian", "Samoan", "Serbian", "Shona", "Sindhi", "Sinhala", "Slovak",
    "Slovenian", "Somali", "Spanish", "Sundanese", "Swahili", "Swedish",
    "Tagalog", "Tajik", "Tamil", "Telugu", "Thai", "Tibetan", "Turkish",
    "Turkmen", "Ukrainian", "Urdu", "Uzbek", "Vietnamese", "Welsh",
    "Xhosa", "Yoruba", "Zulu"
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
# Configure with BT_ALLOWED_ORIGINS (comma-separated exact origins, e.g.
# "https://books.example.com,http://mynas:8083"). BT_ALLOW_PRIVATE_LAN
# (default true) additionally allows localhost and RFC1918 addresses on any
# port — the common self-hosted case. Note: in proxy-injection mode the overlay
# is same-origin and CORS never comes into play.

ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get(
        "BT_ALLOWED_ORIGINS", "http://localhost:8083,http://localhost:8383"
    ).split(",")
    if o.strip()
}
BT_ALLOW_PRIVATE_LAN = os.environ.get("BT_ALLOW_PRIVATE_LAN", "true").lower() in ("1", "true", "yes")
_PRIVATE_ORIGIN_RE = re.compile(
    r"^https?://("
    r"localhost|127\.0\.0\.1|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)


def _is_origin_allowed(origin: str | None) -> str | None:
    """Return the origin if it's allowed, else None."""
    if not origin:
        return None
    if origin in ALLOWED_ORIGINS:
        return origin
    if BT_ALLOW_PRIVATE_LAN and _PRIVATE_ORIGIN_RE.match(origin):
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

def _client_ip() -> str:
    """Rate-limit key. Uses X-Forwarded-For's first hop only when the peer
    (the IP the WSGI server actually saw, NOT the X-Forwarded-For value) is
    trusted. That means either BT_TRUSTED_PROXIES matches the peer, or
    BT_TRUST_PROXY=true is set (legacy / dev only — anyone who can reach
    the API can spoof X-Forwarded-For in this mode).
    """
    peer = request.remote_addr or "unknown"
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        client_ip_from_xff = fwd.split(",")[0].strip()
    else:
        client_ip_from_xff = ""

    # BT_TRUSTED_PROXIES path: precise allowlist of peer CIDRs.
    if BT_TRUSTED_PROXIES and client_ip_from_xff:
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return peer  # malformed peer — don't trust the XFF
        if any(peer_ip in net for net in _TRUSTED_PROXY_NETS):
            return client_ip_from_xff or peer
        # Peer not in allowlist: an attacker is forging XFF. Use peer.
        return peer

    # Legacy BT_TRUST_PROXY=true: trust XFF from any peer.
    if BT_TRUST_PROXY and client_ip_from_xff:
        return client_ip_from_xff

    return peer


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
        client_ip = _client_ip()
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
        # Let cross-origin JS read the request ID and 429 Retry-After header.
        response.headers["Access-Control-Expose-Headers"] = "X-Request-ID, Retry-After"

    return response


# ── Routes ──────────────────────────────────────────────────────────────────


@app.errorhandler(413)
def request_too_large(_e):
    """Return a clean JSON 413 when MAX_CONTENT_LENGTH trips.

    Without this handler Werkzeug returns an HTML body, which is fine for a
    browser but breaks any API client that JSON-decodes the response. We
    still want the per-field caps to fire first when the body is small but
    contains one oversized value — this is purely the backstop path.
    """
    req_id = getattr(request, "request_id", None)
    return jsonify({
        "error": f"Request body exceeds the {BT_MAX_CONTENT_LENGTH}-byte limit",
        "request_id": req_id,
    }), 413


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
        "version": __version__,
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
    if len(text) > BT_MAX_PARAGRAPH_CHARS:
        return jsonify({
            "error": f"'text' exceeds the {BT_MAX_PARAGRAPH_CHARS}-character limit"
        }), 413

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
    if len(paragraphs) > BT_MAX_BATCH_PARAGRAPHS:
        return jsonify({
            "error": f"Too many paragraphs ({len(paragraphs)}); max {BT_MAX_BATCH_PARAGRAPHS} per request"
        }), 413
    if not all(isinstance(p, str) for p in paragraphs):
        return jsonify({"error": "All 'paragraphs' entries must be strings"}), 400
    oversized = next((i for i, p in enumerate(paragraphs) if len(p) > BT_MAX_PARAGRAPH_CHARS), None)
    if oversized is not None:
        return jsonify({
            "error": f"Paragraph {oversized} exceeds the {BT_MAX_PARAGRAPH_CHARS}-character limit"
        }), 413

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
    """Evict old cache entries. Optional body: {"days": 30}.
    `days` must be an integer >= 1 — a negative value would match every row
    (created_at < future date) and silently wipe the whole cache.

    Auth: when BT_API_TOKEN is set, requires the matching X-BT-Token header.
    Cache cleanup is destructive — without auth, anyone on the LAN could
    wipe the entire cache. (The other translate endpoints are auth-gated
    the same way in before_request_hook; this one is gated here so the
    route's auth requirement is visible at the definition site rather
    than scattered across middleware.)"""
    # Auth check, mirroring the before_request pattern. OPTIONS/health/ping
    # exemptions are not relevant for this method/route.
    if API_TOKEN:
        token = request.headers.get("X-BT-Token", "")
        if token != API_TOKEN:
            return jsonify({
                "error": "Unauthorized",
                "request_id": getattr(request, "request_id", None),
            }), 401

    data = request.get_json(silent=True) or {}
    days = data.get("days", 30)
    if isinstance(days, bool) or not isinstance(days, int) or not (1 <= days <= 3650):
        return jsonify({"error": "'days' must be an integer between 1 and 3650"}), 400
    deleted = cleanup_old_entries(days=days)
    return jsonify({"deleted": deleted, "days": days})


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8390"))
    log.info("Starting book-translator on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
