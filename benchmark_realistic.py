"""Exercise warm and cold live translation batches with explicit authentication.

Set ``BT_API_TOKEN`` for token auth or ``BT_BENCHMARK_COOKIE`` for a CWA
session and point ``--url`` to the corresponding API or ``/bt-api`` base path.
Redirects and inherited HTTP proxy settings are disabled so credentials cannot
silently leave the operator-selected endpoint.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import statistics
import time
from typing import Sequence

import requests

from benchmark import _headers, _http_url, _positive_float


DEFAULT_URL = "http://127.0.0.1:8390"


def translate_batch(
    paragraphs: list[str],
    *,
    base_url: str,
    token: str | None,
    cookie: str | None,
    timeout: float,
    session,
) -> tuple[dict, float]:
    started = time.monotonic()
    response = session.post(
        f"{base_url.rstrip('/')}/translate/batch",
        headers=_headers(token, cookie),
        json={
            "paragraphs": paragraphs,
            "source_lang": "English",
            "target_lang": "Spanish",
        },
        timeout=timeout,
        allow_redirects=False,
    )
    try:
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"unexpected HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("provider returned a non-JSON response") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("translations"), list):
            raise RuntimeError("response does not contain a translations list")
        return payload, time.monotonic() - started
    finally:
        response.close()


def run_benchmark_scenario(
    name: str,
    num_paragraphs: int,
    batch_size: int,
    max_concurrent: int,
    warm: bool = False,
    *,
    base_url: str,
    token: str | None,
    cookie: str | None,
    timeout: float,
    session=None,
) -> None:
    print(f"\n--- Scenario: {name} ---")
    prefix = "WARM_CACHE_STATIC_TEST_STR_" if warm else f"COLD_CACHE_{time.time()}_"
    paragraphs = [
        f"{prefix} Paragraph number {index}. This is a sufficiently long "
        "paragraph to test the real system performance under load."
        for index in range(num_paragraphs)
    ]
    batches = [
        paragraphs[index:index + batch_size]
        for index in range(0, len(paragraphs), batch_size)
    ]
    client = session if session is not None else requests.Session()
    owns_session = session is None
    if owns_session:
        client.trust_env = False
    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = [
                executor.submit(
                    translate_batch,
                    batch,
                    base_url=base_url,
                    token=token,
                    cookie=cookie,
                    timeout=timeout,
                    session=client,
                )
                for batch in batches
            ]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
    finally:
        if owns_session:
            client.close()

    total_time = time.monotonic() - started
    times = [elapsed for _payload, elapsed in results]
    p50 = statistics.median(times) if times else 0
    p95 = statistics.quantiles(times, n=20)[18] if len(times) > 1 else p50
    print(f"Total time: {total_time:.2f}s")
    print(f"Batches (size {batch_size}): {len(batches)}")
    print(f"Concurrency: {max_concurrent}")
    print(f"Throughput: {num_paragraphs / total_time:.2f} paragraphs/s")
    print(f"Batch latency p50: {p50:.2f}s, p95: {p95:.2f}s")
    print("Failures: 0")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", type=_http_url,
        default=os.environ.get("BENCHMARK_URL", DEFAULT_URL),
    )
    parser.add_argument("--token", default=os.environ.get("BT_API_TOKEN"))
    parser.add_argument(
        "--cookie", default=os.environ.get("BT_BENCHMARK_COOKIE"),
        help="CWA Cookie header; defaults to BT_BENCHMARK_COOKIE",
    )
    parser.add_argument("--timeout", type=_positive_float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.token and args.cookie:
        parser.error("--token and --cookie are mutually exclusive")
    scenarios = (
        ("Warming", 10, 5, 2, True),
        ("Warm Cache (Batch 5, Conc 2)", 50, 5, 2, True),
        ("Cold Cache (Batch 1, Conc 1)", 5, 1, 1, False),
        ("Cold Cache (Batch 3, Conc 2)", 15, 3, 2, False),
    )
    try:
        for name, paragraphs, batch_size, concurrency, warm in scenarios:
            run_benchmark_scenario(
                name,
                paragraphs,
                batch_size,
                concurrency,
                warm,
                base_url=args.url,
                token=args.token,
                cookie=args.cookie,
                timeout=args.timeout,
            )
    except (requests.RequestException, RuntimeError) as exc:
        parser.exit(1, f"benchmark failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
