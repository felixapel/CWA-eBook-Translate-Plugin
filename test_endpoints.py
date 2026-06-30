import requests
import json
import time

BASE_URL = 'http://192.168.0.122:8390'

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

def test_prefetch():
    res = requests.post(f'{BASE_URL}/prefetch', json={
        "paragraphs": ["Fourth paragraph.", "Fifth paragraph."],
        "source_lang": "English",
        "target_lang": "Spanish"
    })
    print("Prefetch:", res.status_code, res.json())

if __name__ == '__main__':
    test_health()
    test_translate()
    test_batch()
    test_prefetch()
