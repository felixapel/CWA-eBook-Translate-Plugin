"""
Self-contained backend tests — no live server, no network.

Uses Flask's test client and a mocked LLM (monkeypatched requests.post), so it
runs anywhere with just `flask` + `requests` installed:

    pip install flask requests
    python test_translation.py

Covers: provider fallback, errors not cached, batched-prompt translation
(one LLM call per group) and its per-paragraph fallback on a malformed reply.
"""
import os, sys, json, re, tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "bt_test_translations.db")
os.environ["LLM_PROVIDER"] = "local"
os.environ["LLM_MODEL"] = "fake-model"
os.environ["LLM_FALLBACK_PROVIDER"] = "minimax"
os.environ["LLM_FALLBACK_MODEL"] = "fake-fallback"
os.environ["LLM_FALLBACK_API_KEY"] = "x" * 20
os.environ["BT_MAX_CONCURRENT"] = "2"
os.environ["BT_BATCH_SIZE"] = "3"
for f in (os.environ["DB_PATH"], os.environ["DB_PATH"] + "-wal", os.environ["DB_PATH"] + "-shm"):
    try: os.remove(f)
    except OSError: pass

import requests

STATE = {"local_up": False, "fallback_up": True, "batch_calls": 0, "single_calls": 0, "malform": False}
SEG_RE = re.compile(r"@@\s*SEG\s*(\d+)\s*@@", re.IGNORECASE)


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status; self._body = body; self.text = json.dumps(body)
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}"); err.response = self; raise err


def _segments(user_text):
    matches = list(SEG_RE.finditer(user_text))
    out = []
    for k, m in enumerate(matches):
        start = m.end(); end = matches[k + 1].start() if k + 1 < len(matches) else len(user_text)
        out.append((int(m.group(1)), user_text[start:end].strip()))
    return out


def fake_post(url, headers=None, json=None, timeout=None):
    is_local = "1234" in url or "localhost" in url
    if is_local and not STATE["local_up"]:
        raise requests.exceptions.ConnectionError("local refused")
    if (not is_local) and not STATE["fallback_up"]:
        raise requests.exceptions.ConnectionError("fallback refused")

    system = json["messages"][0]["content"] if json.get("messages") else ""
    user_text = json["messages"][-1]["content"]
    tag = "LOCAL" if is_local else "FB"

    if "@@SEG" in system:
        STATE["batch_calls"] += 1
        segs = _segments(user_text)
        lines = []
        for idx, (n, txt) in enumerate(segs):
            if STATE["malform"] and idx == len(segs) - 1:
                lines.append(f"[{tag}] {txt}")          # missing marker -> parse mismatch
            else:
                lines.append(f"@@SEG {n}@@\n[{tag}] {txt}")
        content = "\n\n".join(lines)
    else:
        STATE["single_calls"] += 1
        content = f"[{tag}] {user_text}"

    if not is_local:  # minimax fallback uses the anthropic response shape
        return FakeResp(200, {"content": [{"type": "text", "text": content}]})
    return FakeResp(200, {"choices": [{"message": {"content": content}}]})


requests.post = fake_post

import server  # noqa: E402
client = server.app.test_client()

failed = []
def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond: failed.append(name)


def run():
    # Single translate falls back to the secondary when local is down.
    d = client.post("/translate", json={"text": "hello"}).get_json()
    check("single: fallback used when local down", d.get("translated") == "[FB] hello")
    d = client.post("/translate", json={"text": "hello"}).get_json()
    check("single: cache hit on repeat", d.get("cached") is True)

    # Batched: 5 paragraphs at BT_BATCH_SIZE=3 -> 2 grouped calls, order preserved.
    STATE["local_up"] = True
    STATE["batch_calls"] = STATE["single_calls"] = 0
    paras = [f"para {i}" for i in range(5)]
    d = client.post("/translate/batch", json={"paragraphs": paras}).get_json()
    check("batch: all 5 translated in order", d["translations"] == [f"[LOCAL] para {i}" for i in range(5)])
    check("batch: 2 grouped LLM calls (not 5)", STATE["batch_calls"] == 2 and STATE["single_calls"] == 0)

    # Malformed segmented reply -> transparent per-paragraph fallback.
    STATE["malform"] = True
    STATE["batch_calls"] = STATE["single_calls"] = 0
    d = client.post("/translate/batch", json={"paragraphs": [f"a{i}" for i in range(3)]}).get_json()
    check("batch-fallback: still all correct", d["translations"] == [f"[LOCAL] a{i}" for i in range(3)])
    check("batch-fallback: used per-paragraph calls", STATE["single_calls"] == 3)
    STATE["malform"] = False

    # Errors not cached; retried after recovery.
    STATE["local_up"] = STATE["fallback_up"] = False
    d = client.post("/translate/batch", json={"paragraphs": ["x", "", "y"]}).get_json()
    check("batch: empty slot preserved + errors marked",
          d["translations"][1] == "" and d["translations"][0].startswith("[TRANSLATION ERROR"))
    STATE["local_up"] = True
    d = client.post("/translate/batch", json={"paragraphs": ["x", "", "y"]}).get_json()
    check("batch: retried after recovery", d["translations"][0] == "[LOCAL] x" and d["translations"][2] == "[LOCAL] y")

    # Context Window.
    os.environ["BT_CONTEXT_WINDOW"] = "1"
    # reload settings by resetting module variable if needed, but translator.py reads it at import time.
    # Because translator is already imported, we'll patch it directly.
    import translator
    translator.BT_CONTEXT_WINDOW = 1
    
    # We mock fake_post to verify context is received
    received_contexts = []
    original_post = requests.post
    def context_check_post(url, headers=None, json=None, timeout=None):
        user_text = json["messages"][-1]["content"]
        received_contexts.append(user_text)
        return fake_post(url, headers, json, timeout)
    
    requests.post = context_check_post
    client.post("/translate/batch", json={"paragraphs": ["uncached_context_para_1", "uncached_context_para_2"]}).get_json()
    requests.post = original_post
    translator.BT_CONTEXT_WINDOW = 0
    
    check("context: previous/next contexts included in batch prompt",
          "[NEXT CONTEXT]" in received_contexts[0] and "[PREVIOUS CONTEXT]" in received_contexts[0])

    # Output token cap is proportional to input and clamped to the ceiling, so a
    # rambling model can't burn thousands of tokens on a short paragraph.
    import translator
    check("output cap: short input stays small (no runaway generation)",
          translator._output_cap("A short sentence.", 4096) < 1000)
    check("output cap: long input clamps to the ceiling",
          translator._output_cap("x" * 100000, 4096) == 4096)
    check("output cap: never below the floor",
          translator._output_cap("", 4096) >= translator.BT_OUTPUT_TOKEN_FLOOR)
    check("output cap: monotonic in input size",
          translator._output_cap("x" * 50, 4096) <= translator._output_cap("x" * 5000, 4096))

    check("invalid language rejected",
          client.post("/translate", json={"text": "x", "target_lang": "Klingon"}).status_code == 400)

    # /ping is an instant liveness probe (no LLM) used by the Docker healthcheck.
    pr = client.get("/ping")
    check("ping returns 200 instantly", pr.status_code == 200 and pr.get_json().get("status") == "ok")

    # Rate Limiting
    server._rate_limit_store.clear()
    limit = server.RATE_LIMIT_MAX
    for i in range(limit):
        client.post("/translate", json={"text": f"rate{i}"})
    resp = client.post("/translate", json={"text": "limit_test"})
    check("rate limit: returns status 429", resp.status_code == 429)
    check("rate limit: response has Retry-After header", "Retry-After" in resp.headers)
    check("rate limit: response JSON has retry_after", resp.get_json().get("retry_after") is not None)
    server._rate_limit_store.clear()


if __name__ == "__main__":
    run()
    print("\nRESULT:", "ALL PASS" if not failed else f"FAILED: {failed}")
    sys.exit(1 if failed else 0)
