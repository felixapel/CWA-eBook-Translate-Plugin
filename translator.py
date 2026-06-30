"""
book-translator — Unified Multi-provider translation
Supports OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter, and Local LLMs.
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

# Legacy fallbacks
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
    "local": (LOCAL_BACKEND_URL, "openai")
}

# ── Lazy-loaded API key ──────────────────────────────────────────────────────
_api_key: Optional[str] = None

def _load_api_key() -> str:
    """Load API key from env, or legacy configs."""
    if LLM_API_KEY and len(LLM_API_KEY) > 1:
        log.info(f"Loaded API key from LLM_API_KEY env var")
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
            key = auth.get("credential_pool", {}).get(LLM_PROVIDER, auth.get("credential_pool", {}).get("minimax", ""))
            if key: return key
        except Exception:
            pass

    env_path = Path(".env")
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{LLM_PROVIDER.upper()}_API_KEY=") or line.startswith("MINIMAX_API_KEY="):
                        if not line.startswith("#"):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
            
    return ""

def _get_api_key() -> str:
    global _api_key
    if _api_key is None:
        _api_key = _load_api_key()
    return _api_key

# ── Prompts ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a professional literary translator. Translate the following text from {source_lang} to {target_lang}.

Rules:
1. Preserve ALL formatting: paragraphs, line breaks, quotes, italics markers (*text*), bold markers (**text**).
2. Maintain the author's voice, tone, and style. Literary quality is paramount.
3. Do NOT add any commentary, notes, or explanations.
4. Return ONLY the translated text, nothing else."""

def get_cache_key(text: str, source_lang: str, target_lang: str) -> str:
    content = f"{text}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

# ── Translation logic ──────────────────────────────────────────────────────

def _translate_openai(url: str, text: str, source_lang: str, target_lang: str, timeout: int = 60) -> str:
    headers = {"Content-Type": "application/json"}
    api_key = _get_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": LLM_MODEL,
        "temperature": 0.3,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang),
            },
            {"role": "user", "content": text},
        ],
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    
    translated = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not translated:
        raise RuntimeError("Empty response from API")
    return translated

def _translate_anthropic(url: str, text: str, source_lang: str, target_lang: str, timeout: int = 60) -> str:
    api_key = _get_api_key()
    headers = {
        "Content-Type": "application/json",
    }
    
    if "minimax" in url:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"

    payload = {
        "model": LLM_MODEL,
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang),
        "messages": [{"role": "user", "content": text}],
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    
    content = body.get("content", [])
    translated = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
    if not translated:
        raise RuntimeError("Empty response from Anthropic API")
    return translated

def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_retries: int = 2,
    timeout: int = 60,
    prefer_local: bool = True,  # Ignored, preserved for backward compatibility
) -> tuple[str, str]:
    
    provider_info = PROVIDER_ENDPOINTS.get(LLM_PROVIDER)
    if not provider_info:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")
        
    url, api_type = provider_info
    
    last_error = None
    for attempt in range(max_retries):
        try:
            if api_type == "openai":
                translated = _translate_openai(url, text, source_lang, target_lang, timeout)
            else:
                translated = _translate_anthropic(url, text, source_lang, target_lang, timeout)
                
            log.debug(f"Translated %d chars %s→%s via %s", len(text), source_lang, target_lang, LLM_PROVIDER)
            return translated, LLM_PROVIDER
            
        except requests.exceptions.RequestException as e:
            error_body = getattr(e.response, "text", str(e))[:300]
            status_code = getattr(e.response, "status_code", 0)
            log.warning(f"%s HTTP %d (attempt %d/%d): %s", LLM_PROVIDER, status_code, attempt + 1, max_retries, error_body)
            last_error = f"{LLM_PROVIDER}: HTTP {status_code}"
            
            if status_code == 429:
                time.sleep(2 ** attempt)
            elif status_code >= 500:
                time.sleep(1)
            else:
                break
        except Exception as e:
            log.warning("%s failed (attempt %d): %s", LLM_PROVIDER, attempt + 1, e)
            last_error = f"{LLM_PROVIDER}: {e}"
            time.sleep(0.5)

    raise RuntimeError(f"Translation failed after {max_retries} attempts: {last_error}")

def translate_batch(
    texts: list[str],
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_concurrent: int = 3,
) -> list[tuple[str, str]]:
    results = [("", "")] * len(texts)
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

def check_backend_health() -> dict:
    health = {}
    provider_info = PROVIDER_ENDPOINTS.get(LLM_PROVIDER)
    if not provider_info:
        health[LLM_PROVIDER] = {"status": "error", "latency_ms": -1, "error": "Unknown provider"}
        return health
        
    url, api_type = provider_info
    
    try:
        start = time.monotonic()
        if api_type == "openai":
            headers = {"Content-Type": "application/json"}
            if _get_api_key():
                headers["Authorization"] = f"Bearer {_get_api_key()}"
            requests.post(url, headers=headers, json={"model": LLM_MODEL, "messages": [{"role": "user", "content": "Hi"}]}, timeout=5)
        else:
            headers = {"Content-Type": "application/json"}
            if "minimax" in url:
                headers["Authorization"] = f"Bearer {_get_api_key()}"
            else:
                headers["x-api-key"] = _get_api_key()
                headers["anthropic-version"] = "2023-06-01"
            requests.post(url, headers=headers, json={"model": LLM_MODEL, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}, timeout=5)
            
        latency = int((time.monotonic() - start) * 1000)
        health[LLM_PROVIDER] = {"status": "ok", "latency_ms": latency, "error": None}
    except Exception as e:
        health[LLM_PROVIDER] = {"status": "error", "latency_ms": -1, "error": str(e)}

    return health
