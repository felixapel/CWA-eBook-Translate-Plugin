"""
Self-contained tests for the production-hardening batch (2026-07-02).

These tests share state with test_translation.py (DB_PATH, env vars,
request.post monkeypatch) by importing the same modules — do NOT add
a second init_db or recreate the app.

Covers:
  - MAX_CONTENT_LENGTH global backstop (rejects oversize bodies with 413
    before the per-field check)
  - /cache/cleanup requires API_TOKEN when BT_API_TOKEN is set
  - /metrics returns Prometheus-friendly counters
  - rate-limit per-IP (X-Forwarded-For honored only when BT_TRUST_PROXY)

Run with: python3 test_hardening.py
"""
import os, sys, json, tempfile
from pathlib import Path

# Same env-var contract as test_translation.py.
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "bt_test_translations.db")
os.environ["LLM_PROVIDER"] = "local"
os.environ["LLM_MODEL"] = "fake-model"
os.environ["LLM_FALLBACK_PROVIDER"] = "minimax"
os.environ["LLM_FALLBACK_MODEL"] = "fake-fallback"
os.environ["LLM_FALLBACK_API_KEY"] = "x" * 20
os.environ["BT_MAX_CONCURRENT"] = "2"
os.environ["BT_BATCH_SIZE"] = "3"
for f in (os.environ["DB_PATH"], os.environ["DB_PATH"] + "-wal", os.environ["DB_PATH"] + "-shm"):
    try:
        os.remove(f)
    except OSError:
        pass

import requests
import ipaddress

# Re-use the same fake_post from test_translation.py.
import test_translation  # noqa: E402
STATE = test_translation.STATE
fake_post = test_translation.fake_post
requests.post = fake_post

# Import server after fake_post is installed.
import server  # noqa: E402
client = server.app.test_client()

failed = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        failed.append(name)


def run():
    # ─────────────────────────────────────────────────────────────────────
    # H1: MAX_CONTENT_LENGTH backstop
    # ─────────────────────────────────────────────────────────────────────
    server.BT_MAX_CONTENT_LENGTH = 1024  # 1 KB
    server.app.config["MAX_CONTENT_LENGTH"] = 1024

    big_paragraph = "x" * 2048  # 2 KB, well over the 1 KB cap
    r = client.post("/translate", json={"text": big_paragraph})
    check("MAX_CONTENT_LENGTH: oversize body rejected with 413",
          r.status_code == 413)
    try:
        body = r.get_json()
        check("MAX_CONTENT_LENGTH: 413 body is JSON",
              isinstance(body, dict) and "error" in body)
    except Exception:
        check("MAX_CONTENT_LENGTH: 413 body is JSON", False)

    r = client.post("/translate/batch", json={"paragraphs": [big_paragraph]})
    check("MAX_CONTENT_LENGTH: /translate/batch also trips 413",
          r.status_code == 413)

    # Restore the production default so the rest of the suite is unaffected.
    server.BT_MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    server.app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    # ─────────────────────────────────────────────────────────────────────
    # H2: /cache/cleanup always requires auth (fail-safe)
    # ─────────────────────────────────────────────────────────────────────
    # Three sources of truth for the token, in order:
    #   1. BT_API_TOKEN (set by operator for the whole API)
    #   2. Auto-generated /app/data/cleanup_token (fail-safe when #1 is unset)
    # The endpoint is NEVER open, even when the operator forgets to set
    # BT_API_TOKEN — that's the whole point of the auto-gen path.

    # Path 1: BT_API_TOKEN is set — that token is the only one accepted.
    original_token = server.API_TOKEN
    original_cached = server._cleanup_token_cache
    server.API_TOKEN = "test-secret-token-please-rotate"
    server._cleanup_token_cache = None
    try:
        r = client.post("/cache/cleanup", json={"days": 1})
        check("/cache/cleanup: unauthenticated rejected with 401",
              r.status_code == 401)

        r = client.post("/cache/cleanup",
                        json={"days": 30},
                        headers={"X-BT-Token": "test-secret-token-please-rotate"})
        check("/cache/cleanup: authenticated call accepted (BT_API_TOKEN)",
              r.status_code == 200 and "deleted" in r.get_json())

        r = client.post("/cache/cleanup",
                        json={"days": 30},
                        headers={"X-BT-Token": "wrong"})
        check("/cache/cleanup: wrong token rejected with 401",
              r.status_code == 401)
    finally:
        server.API_TOKEN = original_token
        server._cleanup_token_cache = original_cached

    # Path 2: BT_API_TOKEN unset. The endpoint must STILL require auth
    # via the auto-generated /app/data/cleanup_token (or in-memory fallback
    # if the file system refuses writes). In the test environment, the
    # data dir is /tmp/bt_test_translations.db's dir, which is writable.
    # We monkeypatch the token to a known value so we can assert behaviour.
    server.API_TOKEN = ""
    server._cleanup_token_cache = None
    server._CLEANUP_TOKEN_PATH = Path(tempfile.gettempdir()) / "bt_test_cleanup_token"
    if server._CLEANUP_TOKEN_PATH.exists():
        server._CLEANUP_TOKEN_PATH.unlink()
    # Set a known token by writing the file directly. This simulates a
    # previous process run that already created the file.
    server._CLEANUP_TOKEN_PATH.write_text("test-autogen-token-abc")

    r = client.post("/cache/cleanup", json={"days": 1})
    check("/cache/cleanup: no BT_API_TOKEN, no header -> 401",
          r.status_code == 401)

    r = client.post("/cache/cleanup",
                    json={"days": 1},
                    headers={"X-BT-Token": "test-autogen-token-abc"})
    check("/cache/cleanup: no BT_API_TOKEN, file token works -> 200",
          r.status_code == 200)

    r = client.post("/cache/cleanup",
                    json={"days": 1},
                    headers={"X-BT-Token": "wrong"})
    check("/cache/cleanup: no BT_API_TOKEN, wrong file token -> 401",
          r.status_code == 401)
    server._CLEANUP_TOKEN_PATH.unlink()
    server._cleanup_token_cache = None

    # ─────────────────────────────────────────────────────────────────────
    # H3: /metrics Prometheus-friendly counters
    # ─────────────────────────────────────────────────────────────────────
    for i in range(5):
        client.post("/translate", json={"text": f"h3_metric_{i}"})

    r = client.get("/metrics")
    check("/metrics: returns 200", r.status_code == 200)
    body = r.get_json()
    check("/metrics: all counters present",
          all(k in body for k in [
              "total_requests", "average_latency_ms", "cache_hit_rate_pct",
              "cache_hits", "cache_misses", "errors"]))
    check("/metrics: total_requests is non-negative int",
          isinstance(body["total_requests"], int) and body["total_requests"] >= 0)
    check("/metrics: cache_hit_rate_pct is a percentage in [0, 100]",
          0.0 <= body["cache_hit_rate_pct"] <= 100.0)
    check("/metrics: total = hits + misses (invariants)",
          body["total_requests"] == body["cache_hits"] + body["cache_misses"])

    # ─────────────────────────────────────────────────────────────────────
    # H4: rate-limit is per-IP
    # ─────────────────────────────────────────────────────────────────────
    server._rate_limit_store.clear()
    server.BT_TRUST_PROXY = False
    client.post("/translate", json={"text": "h4_test_a"},
                headers={"X-Forwarded-For": "1.2.3.4"})
    keys = list(server._rate_limit_store.keys())
    check("rate-limit: without BT_TRUST_PROXY, X-Forwarded-For is ignored",
          keys and keys[0] != "1.2.3.4")
    server._rate_limit_store.clear()

    server.BT_TRUST_PROXY = True
    client.post("/translate", json={"text": "h4_test_b"},
                headers={"X-Forwarded-For": "5.6.7.8, 10.0.0.1"})
    keys = list(server._rate_limit_store.keys())
    check("rate-limit: with BT_TRUST_PROXY, X-Forwarded-For first hop is key",
          keys and keys[0] == "5.6.7.8")
    server.BT_TRUST_PROXY = False
    server._rate_limit_store.clear()

    # ─────────────────────────────────────────────────────────────────────
    # H5: BT_TRUSTED_PROXIES allowlist path (preferred over BT_TRUST_PROXY)
    # ─────────────────────────────────────────────────────────────────────
    # When BT_TRUSTED_PROXIES is set, the rate-limit key uses X-Forwarded-For
    # only if the *peer* (the actual socket source) is in the allowlist.
    # This is the production-safe path; BT_TRUST_PROXY=true (tested in H4)
    # is for dev/local only.
    server._rate_limit_store.clear()
    server.BT_TRUST_PROXY = False
    original_trusted = set(server.BT_TRUSTED_PROXIES)
    original_nets = list(server._TRUSTED_PROXY_NETS)
    try:
        # Werkzeug's test client connects from 127.0.0.1. Allowlist it.
        server.BT_TRUSTED_PROXIES = {"127.0.0.1/32"}
        server._TRUSTED_PROXY_NETS = [
            ipaddress.ip_network("127.0.0.1/32", strict=False)
        ]
        client.post("/translate", json={"text": "h5_test_a"},
                    headers={"X-Forwarded-For": "9.9.9.9"})
        keys = list(server._rate_limit_store.keys())
        check("BT_TRUSTED_PROXIES: peer in allowlist honors XFF",
              keys and keys[0] == "9.9.9.9")
        server._rate_limit_store.clear()

        # Now switch to an allowlist that does NOT match the peer. The
        # client is still 127.0.0.1, but the allowlist is 10.0.0.0/8. The
        # XFF must be ignored — the key falls back to the peer.
        server.BT_TRUSTED_PROXIES = {"10.0.0.0/8"}
        server._TRUSTED_PROXY_NETS = [
            ipaddress.ip_network("10.0.0.0/8", strict=False)
        ]
        client.post("/translate", json={"text": "h5_test_b"},
                    headers={"X-Forwarded-For": "9.9.9.9"})
        keys = list(server._rate_limit_store.keys())
        check("BT_TRUSTED_PROXIES: peer NOT in allowlist ignores XFF (anti-spoof)",
              keys and keys[0] != "9.9.9.9")
    finally:
        server.BT_TRUSTED_PROXIES = original_trusted
        server._TRUSTED_PROXY_NETS = original_nets
        server._rate_limit_store.clear()


if __name__ == "__main__":
    run()
    print("\nRESULT:", "ALL PASS" if not failed else f"FAILED: {failed}")
    sys.exit(1 if failed else 0)