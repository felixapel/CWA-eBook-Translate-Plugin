import requests
import time
import concurrent.futures
import json
import statistics
import os

# Point at your own API: BENCHMARK_URL=http://192.168.1.x:8390 python benchmark_realistic.py
BASE_URL = os.environ.get("BENCHMARK_URL", "http://127.0.0.1:8390")

def translate_batch(paragraphs):
    start = time.time()
    try:
        res = requests.post(f'{BASE_URL}/translate/batch', json={
            "paragraphs": paragraphs,
            "source_lang": "English",
            "target_lang": "Spanish"
        }, timeout=120)
        res.raise_for_status()
        data = res.json()
        end = time.time()
        return data, end - start
    except Exception as e:
        return {"error": str(e)}, time.time() - start

def run_benchmark_scenario(name, num_paragraphs, batch_size, max_concurrent, warm=False):
    print(f"\n--- Scenario: {name} ---")
    
    # Generate paragraphs
    prefix = "WARM_CACHE_STATIC_TEST_STR_" if warm else f"COLD_CACHE_{time.time()}_"
    paragraphs = [f"{prefix} Paragraph number {i}. This is a sufficiently long paragraph to test the real system performance under load." for i in range(num_paragraphs)]
    
    batches = [paragraphs[i:i + batch_size] for i in range(0, len(paragraphs), batch_size)]
    
    start_total = time.time()
    results = []
    times = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(translate_batch, b): b for b in batches}
        for future in concurrent.futures.as_completed(futures):
            res, elapsed = future.result()
            results.append(res)
            times.append(elapsed)
    
    total_time = time.time() - start_total
    
    failures = sum(1 for r in results if "error" in r or "translations" not in r)
    successes = len(results) - failures
    
    if times:
        p50 = statistics.median(times)
        p95 = statistics.quantiles(times, n=20)[18] if len(times) > 1 else times[0]
    else:
        p50, p95 = 0, 0
        
    print(f"Total time: {total_time:.2f}s")
    print(f"Batches (size {batch_size}): {len(batches)}")
    print(f"Concurrency: {max_concurrent}")
    print(f"Throughput: {num_paragraphs / total_time:.2f} paragraphs/s")
    print(f"Batch latency p50: {p50:.2f}s, p95: {p95:.2f}s")
    print(f"Failures: {failures}")
    
    if successes > 0:
        first_res = [r for r in results if "translations" in r][0]
        print(f"Cache hit rate example (from one batch): {first_res.get('cached_count', 0)} / {len(first_res.get('translations', []))}")

if __name__ == '__main__':
    print("Pre-warming cache...")
    run_benchmark_scenario("Warming", 10, 5, 2, warm=True)
    
    run_benchmark_scenario("Warm Cache (Batch 5, Conc 2)", 50, 5, 2, warm=True)
    run_benchmark_scenario("Cold Cache (Batch 1, Conc 1)", 5, 1, 1, warm=False)
    run_benchmark_scenario("Cold Cache (Batch 3, Conc 2)", 15, 3, 2, warm=False)
