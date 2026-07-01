"""
book-translator — Unified Multi-provider translation
Supports OpenAI, Anthropic, Gemini, Groq, Together, MiniMax, DeepSeek, OpenRouter, and Local LLMs.
A primary provider plus an OPTIONAL fallback provider for resilience when a
local LLM is slow or temporarily unavailable.

Batched translation: multiple paragraphs can be translated in a SINGLE LLM call
(see BT_BATCH_SIZE) which is far faster on slow local models. If the model's
segmented response can't be parsed cleanly, the batch transparently falls back
to one-call-per-paragraph so correctness is never sacrificed for speed.
"""
import os
import re
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
LLM_FALLBACK_PROVIDER = os.environ.get("LLM_FALLBACK_PROVIDER", "").lower()
LLM_FALLBACK_API_KEY = os.environ.get("LLM_FALLBACK_API_KEY", "")
LLM_FALLBACK_MODEL = os.environ.get("LLM_FALLBACK_MODEL", "")

# Tunables.
#   BT_TIMEOUT        seconds before a single request is abandoned
#   BT_MAX_CONCURRENT simultaneous requests (batches). For a slow single-GPU
#                     model, 1–2 is more stable than 3.
#   BT_BATCH_SIZE     paragraphs translated per LLM call. >1 is dramatically
#                     faster on slow models; 1 = one call per paragraph (legacy).
BT_TIMEOUT = int(os.environ.get("BT_TIMEOUT", "60"))
BT_MAX_CONCURRENT = int(os.environ.get("BT_MAX_CONCURRENT", "2"))
BT_BATCH_SIZE = int(os.environ.get("BT_BATCH_SIZE", "5"))
# Token ceilings (hard upper bounds). The ACTUAL max_tokens sent per request is
# scaled to the input size (see _output_cap) so a rambling/stuck model can't burn
# thousands of tokens translating a short paragraph — the main cause of 8-20s and
# 120s "read timeout" stalls. The ceilings only apply to genuinely long inputs.
BT_MAX_TOKENS = int(os.environ.get("BT_MAX_TOKENS", "4096"))
BT_BATCH_MAX_TOKENS = int(os.environ.get("BT_BATCH_MAX_TOKENS", "8192"))
# Output budget = input_tokens * FACTOR + FLOOR, clamped to the ceiling above.
# 2.0 is generous (a translation is rarely >2x the source length), so legitimate
# translations are never truncated; it only reins in runaway generation.
BT_OUTPUT_TOKEN_FACTOR = float(os.environ.get("BT_OUTPUT_TOKEN_FACTOR", "2.0"))
BT_OUTPUT_TOKEN_FLOOR = int(os.environ.get("BT_OUTPUT_TOKEN_FLOOR", "256"))
BT_CONTEXT_WINDOW = int(os.environ.get("BT_CONTEXT_WINDOW", "0"))


def _estimate_tokens(text: str) -> int:
    """Rough chars→tokens estimate (~3.5 chars/token for mixed Latin text)."""
    return max(1, int(len(text) / 3.5))


def _output_cap(input_text: str, ceiling: int) -> int:
    """max_tokens proportional to input size, clamped to [FLOOR, ceiling]."""
    budget = int(_estimate_tokens(input_text) * BT_OUTPUT_TOKEN_FACTOR) + BT_OUTPUT_TOKEN_FLOOR
    return max(BT_OUTPUT_TOKEN_FLOOR, min(ceiling, budget))

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

BATCH_SYSTEM_PROMPT = """You are a professional literary translator. You will receive several text segments. Each segment to be translated is introduced by a marker line that looks EXACTLY like `@@SEG N@@` (where N is a number).

Translate EACH marked segment from {source_lang} to {target_lang}.

Rules:
1. Output the SAME marker lines `@@SEG N@@`, in the SAME order, each immediately followed by that segment's translation on the next line(s).
2. Translate EVERY marked segment. NEVER merge, drop, reorder, or renumber segments.
3. Preserve formatting within each segment (line breaks, quotes, *italics*, **bold**).
4. Do NOT translate [CONTEXT] blocks. They are only provided to help you understand the surrounding story.
5. Output ONLY the markers and their translations — no commentary, notes, or explanations."""

_SEG_RE = re.compile(r"@@\s*SEG\s*(\d+)\s*@@", re.IGNORECASE)


def get_cache_key(text: str, source_lang: str, target_lang: str) -> str:
    content = f"{text}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Per-provider request helpers ─────────────────────────────────────────────

def _translate_openai(p: _Provider, user_content: str, system_prompt: str, timeout: int, max_tokens: int) -> str:
    headers = {"Content-Type": "application/json"}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"

    payload = {
        "model": p.model,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    resp = requests.post(p.url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    translated = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not translated:
        raise RuntimeError("Empty response from API")
    return translated


def _translate_anthropic(p: _Provider, user_content: str, system_prompt: str, timeout: int, max_tokens: int) -> str:
    headers = {"Content-Type": "application/json"}
    if "minimax" in p.url:
        headers["Authorization"] = f"Bearer {p.api_key}"
    else:
        headers["x-api-key"] = p.api_key
        headers["anthropic-version"] = "2023-06-01"

    payload = {
        "model": p.model,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }

    resp = requests.post(p.url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    content = body.get("content", [])
    translated = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
    if not translated:
        raise RuntimeError("Empty response from Anthropic API")
    return translated


def _call_provider(p: _Provider, user_content: str, system_prompt: str,
                   max_retries: int, timeout: int, max_tokens: int) -> str:
    """Call one provider with retry/backoff. Raises on definitive failure."""
    last_error = None
    for attempt in range(max_retries):
        try:
            if p.api_type == "openai":
                return _translate_openai(p, user_content, system_prompt, timeout, max_tokens)
            return _translate_anthropic(p, user_content, system_prompt, timeout, max_tokens)
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


def _complete(user_content: str, system_prompt: str, max_retries: int = 2,
              timeout: Optional[int] = None, max_tokens: int = BT_MAX_TOKENS) -> tuple[str, str]:
    """Run a completion through the primary provider, falling back to the secondary."""
    if timeout is None:
        timeout = BT_TIMEOUT

    providers = [_get_primary()]
    fb = _get_fallback()
    if fb is not None:
        providers.append(fb)

    last_error = None
    for p in providers:
        try:
            out = _call_provider(p, user_content, system_prompt, max_retries, timeout, max_tokens)
            return out, p.name
        except Exception as e:
            last_error = f"{p.name}: {e}"
            log.warning("Provider %s exhausted: %s", p.name, e)

    raise RuntimeError(f"Translation failed (all providers): {last_error}")


def translate_text(
    text: str,
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_retries: int = 2,
    timeout: Optional[int] = None,
    prefer_local: bool = True,  # Ignored, preserved for backward compatibility
) -> tuple[str, str]:
    """Translate a single text. Returns (translated_text, provider_name)."""
    system = SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang)
    translated, provider = _complete(text, system, max_retries, timeout, _output_cap(text, BT_MAX_TOKENS))
    log.debug("Translated %d chars %s→%s via %s", len(text), source_lang, target_lang, provider)
    return translated, provider


# ── Batched translation ──────────────────────────────────────────────────────

def _parse_segments(output: str, n: int) -> Optional[list[str]]:
    """Split a batched response back into n segment translations, or None on mismatch."""
    matches = list(_SEG_RE.finditer(output))
    if not matches:
        return None
    by_num = {}
    for k, m in enumerate(matches):
        num = int(m.group(1))
        start = m.end()
        end = matches[k + 1].start() if k + 1 < len(matches) else len(output)
        seg = output[start:end].strip()
        if seg:
            by_num[num] = seg
    out = []
    for i in range(1, n + 1):
        if i not in by_num:
            return None  # a segment is missing/empty — treat the whole batch as failed
        out.append(by_num[i])
    return out


def _translate_group(all_texts: list[str], idxs: list[int], source_lang: str, target_lang: str) -> list[str]:
    """
    Translate a group of paragraphs in ONE LLM call. Falls back to per-paragraph
    translation if the segmented response can't be parsed (count mismatch, dropped
    segment, etc.) so correctness is preserved.
    """
    group_texts = [all_texts[i] for i in idxs]
    if len(group_texts) == 1 and BT_CONTEXT_WINDOW == 0:
        return [translate_text(group_texts[0], source_lang, target_lang)[0]]

    combined_parts = []
    for k, i in enumerate(idxs):
        combined_parts.append(f"@@SEG {k + 1}@@\n{all_texts[i]}")
        
        # Add context if enabled
        if BT_CONTEXT_WINDOW > 0:
            ctx_parts = []
            if i > 0:
                ctx_parts.append(f"[PREVIOUS CONTEXT]: {all_texts[max(0, i - BT_CONTEXT_WINDOW):i]}")
            if i < len(all_texts) - 1:
                ctx_parts.append(f"[NEXT CONTEXT]: {all_texts[i + 1:min(len(all_texts), i + 1 + BT_CONTEXT_WINDOW)]}")
            if ctx_parts:
                combined_parts.append("\n".join(ctx_parts))

    combined = "\n\n".join(combined_parts)
    system = BATCH_SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang)

    try:
        output, _ = _complete(combined, system, max_retries=2, max_tokens=_output_cap(combined, BT_BATCH_MAX_TOKENS))
        parsed = _parse_segments(output, len(group_texts))
    except Exception as e:
        log.warning("Batch call failed (%d segs): %s — falling back to per-paragraph", len(group_texts), e)
        parsed = None

    if parsed is not None:
        return parsed

    log.info("Batch parse mismatch for %d segments; translating individually", len(group_texts))
    out = []
    for t in group_texts:
        try:
            out.append(translate_text(t, source_lang, target_lang)[0])
        except Exception as e:
            log.error("Per-paragraph fallback failed: %s", e)
            out.append(f"[TRANSLATION ERROR: {e}]")
    return out



def translate_batch(
    texts: list[str],
    source_lang: str = "English",
    target_lang: str = "Spanish",
    max_concurrent: Optional[int] = None,
) -> list[tuple[str, str]]:
    """
    Translate multiple texts. Non-empty texts are grouped into batches of
    BT_BATCH_SIZE and each batch is translated in a single LLM call; batches run
    concurrently (up to max_concurrent). Returns a (text, provider) per input.
    """
    if max_concurrent is None:
        max_concurrent = BT_MAX_CONCURRENT
    max_concurrent = max(1, max_concurrent)
    batch_size = max(1, BT_BATCH_SIZE)

    results: list[tuple[str, str]] = [("", "")] * len(texts)
    work = [(i, t) for i, t in enumerate(texts) if t.strip()]
    if not work:
        return results

    # Split the work into groups of batch_size, preserving original indices.
    groups = [work[k:k + batch_size] for k in range(0, len(work), batch_size)]

    work_texts = [t for _, t in work]
    def _do_group(group):
        idxs = [i for i, _ in group]
        gtexts = [t for _, t in group]
        try:
            translations = _translate_group(texts, idxs, source_lang, target_lang)
        except Exception as e:
            log.error("Group translation failed: %s", e)
            translations = [f"[TRANSLATION ERROR: {e}]"] * len(gtexts)
        return idxs, translations

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = [executor.submit(_do_group, g) for g in groups]
        for future in as_completed(futures):
            idxs, translations = future.result()
            for j, idx in enumerate(idxs):
                tr = translations[j] if j < len(translations) else "[TRANSLATION ERROR: missing segment]"
                results[idx] = (tr, LLM_PROVIDER)

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
