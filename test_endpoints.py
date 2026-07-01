"""
Smoke test against a LIVE backend (not mocked — start the API first).
For a self-contained test with no live server, use test_translation.py instead.

    pip install requests
    BENCHMARK_URL=http://127.0.0.1:8390 python test_endpoints.py
"""
import os
import requests

BASE_URL = os.environ.get("BENCHMARK_URL", "http://127.0.0.1:8390")

def test_ping():
    res = requests.get(f'{BASE_URL}/ping')
    print("Ping:", res.status_code, res.json())

def test_health():
    res = requests.get(f'{BASE_URL}/health')
    print("Health:", res.status_code, res.json())

def test_translate():
    res = requests.post(f'{BASE_URL}/translate', json={
        "text": "Hello, this is a test of the translation system.",
        "source_lang": "English",
        "target_lang": "Spanish"
    })
    print("Translate:", res.status_code, res.json())

def test_batch():
    res = requests.post(f'{BASE_URL}/translate/batch', json={
        "paragraphs": ["First paragraph.", "Second paragraph.", "Third paragraph."],
        "source_lang": "English",
        "target_lang": "Spanish"
    })
    print("Batch:", res.status_code, res.json())

if __name__ == '__main__':
    test_ping()
    test_health()
    test_translate()
    test_batch()
