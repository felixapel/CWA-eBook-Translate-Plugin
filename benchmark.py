import os
import requests
import time
import concurrent.futures

# Point at your own API: BENCHMARK_URL=http://192.168.1.x:8390 python benchmark.py
BASE_URL = os.environ.get("BENCHMARK_URL", "http://127.0.0.1:8390")

def make_request(i):
    res = requests.post(f'{BASE_URL}/translate', json={
        "text": f"This is test paragraph number {i}.",
        "source_lang": "English",
        "target_lang": "Spanish"
    })
    return res.json()

def run_benchmark(n_requests, max_workers):
    print(f"Starting benchmark with {n_requests} requests and {max_workers} workers...")
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(make_request, i) for i in range(n_requests)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Completed in {elapsed:.2f} seconds.")
    print(f"Throughput: {n_requests / elapsed:.2f} req/s")
    
    # Analyze latency
    latencies = [r.get('elapsed_ms', 0) for r in results if not r.get('cached', False)]
    if latencies:
        print(f"Average fresh latency: {sum(latencies)/len(latencies):.0f}ms")
    
if __name__ == '__main__':
    run_benchmark(n_requests=80, max_workers=10)
