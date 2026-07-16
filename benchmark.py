"""Run a small authenticated load test against a live translator endpoint.

Use the same-origin proxy path for ``cwa_session`` deployments, for example::

    BT_BENCHMARK_COOKIE='session=...' \
    BT_BENCHMARK_USER_AGENT='the exact browser User-Agent' python benchmark.py \
      --url https://books.example.test/bt-api

Use ``BT_API_TOKEN`` instead for token-authenticated API endpoints. Credentials
are sent only to the exact URL supplied by the operator: redirects and inherited
HTTP proxy settings are disabled.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import os
import time
from typing import Sequence
from urllib.parse import urlsplit

import requests


DEFAULT_URL = "http://127.0.0.1:8390"


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return value


def _http_url(raw: str) -> str:
    if not raw or raw != raw.strip() or any(character.isspace() for character in raw):
        raise argparse.ArgumentTypeError("must be one HTTP(S) base URL")
    try:
        parsed = urlsplit(raw)
        parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a valid HTTP(S) base URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise argparse.ArgumentTypeError("must be an HTTP(S) base URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise argparse.ArgumentTypeError("must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("must not contain a query or fragment")
    return raw


def _user_agent(raw: str) -> str:
    if (
        not raw
        or raw != raw.strip()
        or len(raw.encode("utf-8")) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise argparse.ArgumentTypeError(
            "must be the exact non-empty browser User-Agent without control characters"
        )
    return raw


def _headers(
    token: str | None,
    cookie: str | None,
    user_agent: str | None = None,
) -> dict[str, str]:
    if token and (cookie or user_agent):
        raise RuntimeError("token and CWA cookie authentication are mutually exclusive")
    if token:
        return {"X-BT-Token": token}
    if cookie:
        if not user_agent:
            raise RuntimeError("CWA cookie authentication requires the exact browser User-Agent")
        return {"Cookie": cookie, "User-Agent": _user_agent(user_agent)}
    if user_agent:
        raise RuntimeError("a browser User-Agent is valid only with CWA cookie authentication")
    return {}


def make_request(
    index: int,
    *,
    base_url: str,
    token: str | None,
    cookie: str | None,
    timeout: float,
    session,
    user_agent: str | None = None,
) -> dict:
    response = session.post(
        f"{base_url.rstrip('/')}/translate",
        headers=_headers(token, cookie, user_agent),
        json={
            "text": f"This is test paragraph number {index}.",
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
            return response.json()
        except ValueError as exc:
            raise RuntimeError("provider returned a non-JSON response") from exc
    finally:
        response.close()


def run_benchmark(
    n_requests: int,
    max_workers: int,
    *,
    base_url: str,
    token: str | None,
    cookie: str | None,
    timeout: float,
    session=None,
    user_agent: str | None = None,
) -> None:
    client = session if session is not None else requests.Session()
    owns_session = session is None
    if owns_session:
        client.trust_env = False
    print(f"Starting benchmark with {n_requests} requests and {max_workers} workers...")
    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    make_request,
                    index,
                    base_url=base_url,
                    token=token,
                    cookie=cookie,
                    user_agent=user_agent,
                    timeout=timeout,
                    session=client,
                )
                for index in range(n_requests)
            ]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
    finally:
        if owns_session:
            client.close()

    elapsed = time.monotonic() - started
    print(f"Completed in {elapsed:.2f} seconds.")
    print(f"Throughput: {n_requests / elapsed:.2f} req/s")
    latencies = [
        result.get("elapsed_ms", 0)
        for result in results
        if not result.get("cached", False)
    ]
    if latencies:
        print(f"Average fresh latency: {sum(latencies) / len(latencies):.0f}ms")


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
    parser.add_argument(
        "--user-agent",
        type=_user_agent,
        default=os.environ.get("BT_BENCHMARK_USER_AGENT"),
        help="exact login-time browser User-Agent; defaults to BT_BENCHMARK_USER_AGENT",
    )
    parser.add_argument("--requests", type=_positive_int, default=80)
    parser.add_argument("--workers", type=_positive_int, default=10)
    parser.add_argument("--timeout", type=_positive_float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.token and args.cookie:
        parser.error("--token and --cookie are mutually exclusive")
    if bool(args.cookie) != bool(args.user_agent):
        parser.error("--cookie and --user-agent must be provided together")
    if args.token and args.user_agent:
        parser.error("--user-agent is valid only with --cookie")
    try:
        run_benchmark(
            args.requests,
            args.workers,
            base_url=args.url,
            token=args.token,
            cookie=args.cookie,
            user_agent=args.user_agent,
            timeout=args.timeout,
        )
    except (requests.RequestException, RuntimeError) as exc:
        parser.exit(1, f"benchmark failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
