"""
book-translator — Multi-backend translation: MiniMax-M3 (Anthropic API) + local LLM (OpenAI-compatible).
Primary: LM Studio gemma-4-12b-agentic (fast, free, local)
Fallback: MiniMax-M3 (premium quality, paid subscription)
"""
import os
import json
import time
import hashlib
import logging
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

log = logging.getLogger("book-translator.translator")

# ── Backend configuration ──────────────────────────────────────────────────

# Primary backend: vLLM on Unraid (ultra-fast, free, local)
LOCAL_BACKEND_URL = os.environ.get(
    "BT_LOCAL_URL",
    "http://192.168.0.122:2819/v1/chat/completions",
)
LOCAL_BACKEND_MODEL = os.environ.get(
    "BT_LOCAL_MODEL",
    "gemma4-12b",
)
LOCAL_BACKEND_ENABLED = os.environ.get("BT_LOCAL_ENABLED", "1") == "1"

# Fallback backend: MiniMax-M3 (paid, premium quality)
MINIMAX_ANTHROPIC_URL = "https://api.minimax.io/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-M3"

# Priority: local first, then MiniMax
BACKEND_ORDER = ["local", "minimax"]

# ── Lazy-loaded API key (C1: no module-level side effects) ─────────────────
_api_key: Optional[str] = None


def _load_api_key() -> str:
    """Load MiniMax API key from auth.json credential pool or .env."""
    # Try auth.json credential pool first (most reliable)
    auth_path = Path("/home/hermes/.hermes/auth.json")
    if auth_path.exists():
        try:
            with open(auth_path) as f:
                auth = json.load(f)
            key = auth.get("credential_pool", {}).get("minimax", "")
            if key and len(key) > 10:
                log.info("Loaded MiniMax API key from auth.json credential pool")
                return key
        except Exception as e:
            log.warning("Failed to load from auth.json: %s", e)
    # Fallback to .env
    env_path = Path("/home/hermes/.hermes/.env")
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MINIMAX_API_KEY=") and not line.startswith("#"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val and len(val) > 10:
                            log.info("Loaded MiniMax API key from .env")
                            return val
        except Exception as e:
            log.warning("Failed to load from .env: %s", e)
    raise RuntimeError(
        "MINIMAX_API_KEY not found in auth.json credential_pool.minimax "
        "or ~/.hermes/.env"
    )


def _get_api_key() -> str:
    """Return the API key, loading it lazily on first use (C1)."""
    global _api_key
    if _api_key is None:
        _api_key = _load_api_key()
    return _api_key


# ── Simplified translation prompts (works on both local LLMs and M3) ───────

SYSTEM_PROMPT_LOCAL = "Translate the following text from {source_lang} to {target_lang}. Return ONLY the translation, no commentary."

SYSTEM_PROMPT_PREMIUM = """You are a professional literary translator. Translate the following text from {source_lang} to {target_lang}. 

Rules:
1. Preserve ALL formatting: paragraphs, line breaks, quotes, italics markers (*text*), bold markers (**text**).
2. Maintain the author's voice, tone, and style. Literary quality is paramount.
3. Do NOT add any commentary, notes, or explanations.
4. Return ONLY the translated text, nothing else."""


def get_cache_key(text: str, source_lang: str, target_lang: str) -> str:
    """Deterministic cache key for a paragraph + language pair."""
    content = f"{text}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _translate_via_local(text: str, source_lang: str, target_lang: str, timeout: int = 60) -> str:
    """Translate using local LM Studio gemma (OpenAI-compatible API)."""
    payload = {
        "model": LOCAL_BACKEND_MODEL,
        "temperature": 0.3,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT_LOCAL.format(
                    source_lang=source_lang, target_lang=target_lang
                ),
            },
            {"role": "user", "content": text},
        ],
    }
    req = urllib.request.Request(
        LOCAL_BACKEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    translated = (
        body.get("choices", [{}])[0].get("message", {}).get("content", "")
    ).strip()
    if not translated:
        raise RuntimeError("Empty response from local LLM")
    return translated


def _translate_via_minimax(text: str, source_lang: str, target_lang: str, timeout: int = 60) -> str:
    """Translate using MiniMax-M3 via Anthropic API."""
    api_key = _get_api_key()  # C1: lazy-loaded
    system_prompt = SYSTEM_PROMPT_PREMIUM.format(
        source_lang=source_lang, target_lang=target_lang
    )
    payload = {
        "model": MINIMAX_MODEL,
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": system_prompt,
        "messages": [{"role": "user", "content": text}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    req = urllib.request.Request(
        MINIMAX_ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body.get("content", [])
    translated = "".join(
        block.get("text", "") for block in content if block.get("type") == "text"
    ).strip()
    if not translated:
        raise RuntimeError("Empty response from MiniMax API")
    return translated


def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_retries: int = 2,
    timeout: int = 60,
    prefer_local: bool = True,
) -> tuple[str, str]:
    """
    Translate text using the best available backend.
    
    Priority: local LM Studio (fast, free) → MiniMax-M3 (premium quality).

    Returns:
        A tuple of (translated_text, backend_name) so the caller knows
        which model was used for cache tracking.
    """
    backends = []
    if prefer_local and LOCAL_BACKEND_ENABLED:
        backends.append(("local", _translate_via_local))
    backends.append(("minimax", _translate_via_minimax))

    last_error = None
    for backend_name, translate_fn in backends:
        for attempt in range(max_retries):
            try:
                translated = translate_fn(text, source_lang, target_lang, timeout)
                log.debug(
                    "Translated %d chars %s→%s via %s (attempt %d)",
                    len(text), source_lang, target_lang, backend_name, attempt + 1,
                )
                return translated, backend_name
            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8")[:300]
                except Exception:
                    pass
                log.warning(
                    "%s HTTP %d (attempt %d/%d): %s",
                    backend_name, e.code, attempt + 1, max_retries, error_body,
                )
                last_error = f"{backend_name}: HTTP {e.code}"
                if e.code == 429:
                    time.sleep(2 ** attempt)
                elif e.code >= 500:
                    time.sleep(1)
                else:
                    break  # Don't retry 4xx (except 429), try next backend
            except Exception as e:
                log.warning("%s failed (attempt %d): %s", backend_name, attempt + 1, e)
                last_error = f"{backend_name}: {e}"
                time.sleep(0.5)

    raise RuntimeError(
        f"Translation failed after trying {len(backends)} backend(s): {last_error}"
    )


def translate_batch(
    texts: list[str],
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_concurrent: int = 3,
) -> list[tuple[str, str]]:
    """
    Translate multiple texts concurrently using ThreadPoolExecutor (M8).

    Returns:
        A list of (translated_text, backend_name) tuples, one per input text.
        Empty strings map to ("", "").
    """
    results: list[tuple[str, str]] = [("", "")] * len(texts)

    # Identify non-empty texts that need translation
    work_items = []
    for i, text in enumerate(texts):
        if text.strip():
            work_items.append((i, text))

    if not work_items:
        return results

    def _do_translate(index: int, text: str) -> tuple[int, str, str]:
        try:
            translated, backend = translate_text(text, source_lang, target_lang)
            return index, translated, backend
        except Exception as e:
            log.error("Batch translation failed at chunk %d: %s", index, e)
            return index, f"[TRANSLATION ERROR: {e}]", "error"

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(_do_translate, idx, txt): idx
            for idx, txt in work_items
        }
        for future in as_completed(futures):
            idx, translated, backend = future.result()
            results[idx] = (translated, backend)

    return results


def check_backend_health() -> dict:
    """
    Ping the local LLM endpoint with a lightweight request and return status.

    Returns a dict with health info for each backend:
        {
            "local": {"status": "ok"|"error", "latency_ms": int, "error": str|None},
            "minimax": {"status": "ok"|"error", ...}
        }
    """
    health = {}

    # Check local backend
    if LOCAL_BACKEND_ENABLED:
        try:
            start = time.monotonic()
            payload = {
                "model": LOCAL_BACKEND_MODEL,
                "temperature": 0,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Hello"}],
            }
            req = urllib.request.Request(
                LOCAL_BACKEND_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            latency = int((time.monotonic() - start) * 1000)
            health["local"] = {"status": "ok", "latency_ms": latency, "error": None}
        except Exception as e:
            health["local"] = {"status": "error", "latency_ms": -1, "error": str(e)}
    else:
        health["local"] = {"status": "disabled", "latency_ms": -1, "error": None}

    # Check MiniMax (just verify key is loadable, don't waste API credits)
    try:
        _get_api_key()
        health["minimax"] = {"status": "ok", "latency_ms": 0, "error": None}
    except Exception as e:
        health["minimax"] = {"status": "error", "latency_ms": -1, "error": str(e)}

    return health
