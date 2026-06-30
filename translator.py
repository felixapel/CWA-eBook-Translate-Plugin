"""
book-translator — Unified Multi-provider translation
Supports OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter, and Local LLMs.
A primary provider plus an OPTIONAL fallback provider for resilience when a
local LLM is slow or temporarily unavailable.
"""
import os
import json
import time
import hashlib
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

log = logging.getLogger("book-translator.translator")

# ── Environment Configuration ────────────────────────────────────────────────

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local").lower()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma4-12b")

# Optional fallback provider — used automatically when the primary errors out.
# Leave LLM_FALLBACK_PROVIDER empty to disable.
LLM_FALLBACK_PROVIDER = os.environ.get("LLM_FALLBACK_PROVIDER", "").lower()
LLM_FALLBACK_API_KEY = os.environ.get("LLM_FALLBACK_API_KEY", "")
LLM_FALLBACK_MODEL = os.environ.get("LLM_FALLBACK_MODEL", "")

# Tunables — especially important for slow local LLMs.
#   BT_TIMEOUT        seconds before a single request is abandoned
#   BT_MAX_CONCURRENT simultaneous requests in a batch. For a slow single-GPU
#                     local model, 1–2 is MORE stable than 3 (avoids timeout
#                     cascades from requests starving each other).
BT_TIMEOUT = int(os.environ.get("BT_TIMEOUT", "60"))
BT_MAX_CONCURRENT = int(os.environ.get("BT_MAX_CONCURRENT", "2"))

# Local backend endpoint (only used when a provider == "local").
LOCAL_BACKEND_URL = os.environ.get("BT_LOCAL_URL", "http://localhost:1234/v1/chat/completions")

PROVIDER_ENDPOINTS = {
    "openai": ("https://api.openai.com/v1/chat/completions", "openai"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "anthropic"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "openai"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "openai"),
    "together": ("https://api.together.xyz/v1/chat/completions", "openai"),
    "minimax": ("https://api.minimax.io/anthropic/v1/messages", "anthropic"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "openai"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "openai"),
    "local": (LOCAL_BACKEND_URL, "openai"),
}

# ── API key loading (primary) ────────────────────────────────────────────────

def _load_primary_api_key() -> str:
    """Load the primary API key from env, or legacy auth.json/.env configs."""
    if LLM_API_KEY and len(LLM_API_KEY) > 1:
        log.info("Loaded API key from LLM_API_KEY env var")
        return LLM_API_KEY

    # Legacy fallbacks
    env_key = os.environ.get("MINIMAX_API_KEY")
    if env_key and len(env_key) > 10:
        return env_key

    auth_path = Path("auth.json")
    if auth_path.exists():
        try:
            with open(auth_path) as f:
                auth = json.load(f)
            pool = auth.get("credential_pool", {})
            key = pool.get(LLM_PROVIDER, pool.get("minimax", ""))
            if key:
                return key
        except Exception:
            pass

    env_path = Path(".env")
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        continue
                    if line.startswith(f"{LLM_PROVIDER.upper()}_API_KEY=") or line.startswith("MINIMAX_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    return ""


# ── Provider model ───────────────────────────────────────────────────────────

class _Provider:
    """Resolved configuration for one translation backend."""
    __slots__ = ("name", "url", "api_type", "model", "api_key")

    def __init__(self, name: str, model: str, api_key: str):
        endpoint = PROVIDER_ENDPOINTS.get(name)
        if not endpoint:
            raise ValueError(f"Unknown LLM provider: {name}")
        self.name = name
        self.url, self.api_type = endpoint
        self.model = model
        self.api_key = api_key


_primary_provider: Optional[_Provider] = None
_fallback_provider = "unset"  # sentinel distinct from None (= "no fallback")


def _get_primary() -> _Provider:
    global _primary_provider
    if _primary_provider is None:
        _primary_provider = _Provider(LLM_PROVIDER, LLM_MODEL, _load_primary_api_key())
    return _primary_provider


def _get_fallback() -> Optional[_Provider]:
    global _fallback_provider
    if _fallback_provider == "unset":
        if LLM_FALLBACK_PROVIDER and LLM_FALLBACK_PROVIDER in PROVIDER_ENDPOINTS:
            model = LLM_FALLBACK_MODEL or LLM_MODEL
            if not LLM_FALLBACK_MODEL:
                log.warning(
                    "LLM_FALLBACK_MODEL not set; reusing primary model '%s' for fallback "
                    "provider '%s' (this may be invalid for that provider).",
                    LLM_MODEL, LLM_FALLBACK_PROVIDER,
                )
            _fallback_provider = _Provider(LLM_FALLBACK_PROVIDER, model, LLM_FALLBACK_API_KEY)
            log.info("Fallback provider configured: %s (%s)", LLM_FALLBACK_PROVIDER, model)
        else:
            _fallback_provider = None
    return _fallback_provider


# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a professional literary translator. Translate the following text from {source_lang} to {target_lang}.

Rules:
1. Preserve ALL formatting: paragraphs, line breaks, quotes, italics markers (*text*), bold markers (**text**).
2. Maintain the author's voice, tone, and style. Literary quality is paramount.
3. Do NOT add any commentary, notes, or explanations.
4. Return ONLY the translated text, nothing else."""


def get_cache_key(text: str, source_lang: str, target_lang: str) -> str:
    content = f"{text}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Per-provider request helpers ─────────────────────────────────────────────

def _translate_openai(p: _Provider, text: str, source_lang: str, target_lang: str, timeout: int) -> str:
    headers = {"Content-Type": "application/json"}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"

    payload = {
        "model": p.model,
        "temperature": 0.3,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang)},
            {"role": "user", "content": text},
        ],
    }

    resp = requests.post(p.url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    translated = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not translated:
        raise RuntimeError("Empty response from API")
    return translated


def _translate_anthropic(p: _Provider, text: str, source_lang: str, target_lang: str, timeout: int) -> str:
    headers = {"Content-Type": "application/json"}
    if "minimax" in p.url:
        headers["Authorization"] = f"Bearer {p.api_key}"
    else:
        headers["x-api-key"] = p.api_key
        headers["anthropic-version"] = "2023-06-01"

    payload = {
        "model": p.model,
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang),
        "messages": [{"role": "user", "content": text}],
    }

    resp = requests.post(p.url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    content = body.get("content", [])
    translated = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
    if not translated:
        raise RuntimeError("Empty response from Anthropic API")
    return translated


def _call_provider(p: _Provider, text: str, source_lang: str, target_lang: str,
                   max_retries: int, timeout: int) -> str:
    """Call one provider with retry/backoff. Raises on definitive failure."""
    last_error = None
    for attempt in range(max_retries):
        try:
            if p.api_type == "openai":
                return _translate_openai(p, text, source_lang, target_lang, timeout)
            return _translate_anthropic(p, text, source_lang, target_lang, timeout)
        except requests.exceptions.RequestException as e:
            status_code = getattr(e.response, "status_code", 0)
            error_body = getattr(e.response, "text", str(e))[:300]
            log.warning("%s HTTP %s (attempt %d/%d): %s", p.name, status_code, attempt + 1, max_retries, error_body)
            last_error = f"HTTP {status_code}"
            if status_code == 429:
                time.sleep(2 ** attempt)
            elif status_code and status_code >= 500:
                time.sleep(1)
            else:
                break  # 4xx (other than 429): retrying won't help, bail to fallback
        except Exception as e:
            log.warning("%s failed (attempt %d): %s", p.name, attempt + 1, e)
            last_error = str(e)
            time.sleep(0.5)
    raise RuntimeError(last_error or "unknown error")


def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_retries: int = 2,
    timeout: Optional[int] = None,
    prefer_local: bool = True,  # Ignored, preserved for backward compatibility
) -> tuple[str, str]:
    """
    Translate text using the primary provider, falling back to the optional
    secondary provider if the primary fails.

    Returns (translated_text, provider_name).
    """
    if timeout is None:
        timeout = BT_TIMEOUT

    providers = [_get_primary()]
    fb = _get_fallback()
    if fb is not None:
        providers.append(fb)

    last_error = None
    for p in providers:
        try:
            translated = _call_provider(p, text, source_lang, target_lang, max_retries, timeout)
            log.debug("Translated %d chars %s→%s via %s", len(text), source_lang, target_lang, p.name)
            return translated, p.name
        except Exception as e:
            last_error = f"{p.name}: {e}"
            log.warning("Provider %s exhausted: %s", p.name, e)
            continue

    raise RuntimeError(f"Translation failed (all providers): {last_error}")


def translate_batch(
    texts: list[str],
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_concurrent: Optional[int] = None,
) -> list[tuple[str, str]]:
    if max_concurrent is None:
        max_concurrent = BT_MAX_CONCURRENT
    max_concurrent = max(1, max_concurrent)

    results: list[tuple[str, str]] = [("", "")] * len(texts)
    work_items = [(i, t) for i, t in enumerate(texts) if t.strip()]

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
        futures = {executor.submit(_do_translate, idx, txt): idx for idx, txt in work_items}
        for future in as_completed(futures):
            idx, translated, backend = future.result()
            results[idx] = (translated, backend)

    return results


# ── Health check (cached to avoid hammering the backend) ─────────────────────

_health_cache: dict = {"ts": 0.0, "data": None}
_HEALTH_TTL = 15.0  # seconds


def _probe(p: _Provider) -> dict:
    try:
        start = time.monotonic()
        headers = {"Content-Type": "application/json"}
        payload = {"model": p.model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}
        if p.api_type == "openai":
            if p.api_key:
                headers["Authorization"] = f"Bearer {p.api_key}"
        else:
            if "minimax" in p.url:
                headers["Authorization"] = f"Bearer {p.api_key}"
            else:
                headers["x-api-key"] = p.api_key
                headers["anthropic-version"] = "2023-06-01"
        resp = requests.post(p.url, headers=headers, json=payload, timeout=5)
        resp.raise_for_status()
        latency = int((time.monotonic() - start) * 1000)
        return {"status": "ok", "latency_ms": latency, "error": None}
    except Exception as e:
        return {"status": "error", "latency_ms": -1, "error": str(e)}


def check_backend_health() -> dict:
    now = time.monotonic()
    cached = _health_cache.get("data")
    if cached is not None and (now - _health_cache["ts"]) < _HEALTH_TTL:
        return cached

    health = {}
    try:
        health[_get_primary().name + " (primary)"] = _probe(_get_primary())
    except Exception as e:
        health["primary"] = {"status": "error", "latency_ms": -1, "error": str(e)}

    fb = _get_fallback()
    if fb is not None:
        health[fb.name + " (fallback)"] = _probe(fb)

    _health_cache["data"] = health
    _health_cache["ts"] = now
    return health
