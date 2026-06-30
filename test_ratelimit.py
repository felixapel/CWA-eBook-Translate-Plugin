import requests
import time

BASE_URL = 'http://localhost:8390'

successes = 0
failures = 0
error_text = ""

for i in range(70):
    res = requests.post(f'{BASE_URL}/translate', json={
        "text": f"Test {i}",
        "source_lang": "English",
        "target_lang": "Spanish"
    })
    if res.status_code == 200:
        successes += 1
    elif res.status_code == 429:
        failures += 1
        error_text = res.text
    else:
        print("Unknown status:", res.status_code)

print(f"Successes: {successes}")
print(f"Rate limited (429): {failures}")
print(f"Error text: {error_text}")
