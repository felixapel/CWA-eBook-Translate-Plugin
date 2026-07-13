"""Exercise the rate limiter against a live API without calling a provider.

Start the API with a fresh rate-limit window, then run for example:

    BT_API_TOKEN=... python test_ratelimit.py --url http://127.0.0.1:8390

For the recommended CWA-session proxy, set ``BT_RATE_LIMIT_TEST_COOKIE`` to
the browser cookie header and point ``--url`` at the proxy's ``/bt-api`` path.

The probe deliberately sends English-to-English translations. The request
still traverses authentication and rate limiting, but the endpoint echoes the
input instead of spending provider capacity. The command exits nonzero unless
it observes at least one admitted request, a 429, and no unexpected status.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import os
import sys
from typing import Sequence
from urllib.parse import urlsplit

import requests


DEFAULT_URL = "http://127.0.0.1:8390"


@dataclass(frozen=True)
class RateLimitResult:
    admitted: int
    rate_limited: int
    unexpected: int


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


def exercise_rate_limit(
    base_url: str,
    *,
    token: str | None,
    request_count: int,
    timeout: float,
    cookie: str | None = None,
    session=None,
) -> RateLimitResult:
    """Return live-probe counts, stopping on the first 429 or error."""
    client = session if session is not None else requests.Session()
    owns_session = session is None
    if owns_session:
        # Tokens and session cookies must never traverse operator-configured
        # HTTP(S)_PROXY values inherited from the shell.
        client.trust_env = False
    headers = {}
    if token:
        headers["X-BT-Token"] = token
    if cookie:
        headers["Cookie"] = cookie
    admitted = 0
    rate_limited = 0
    unexpected = 0

    try:
        for index in range(request_count):
            try:
                response = client.post(
                    f"{base_url.rstrip('/')}/translate",
                    headers=headers,
                    json={
                        "text": f"rate-limit-probe-{index}",
                        "source_lang": "English",
                        "target_lang": "English",
                    },
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                unexpected += 1
                print(
                    f"request {index + 1} failed: {type(exc).__name__}",
                    file=sys.stderr,
                )
                break

            try:
                status_code = response.status_code
            finally:
                # The probe never needs a response body. Closing a streamed
                # response bounds memory and connection lifetime even on 4xx.
                response.close()

            if status_code == 200:
                admitted += 1
            elif status_code == 429:
                rate_limited += 1
                break
            else:
                unexpected += 1
                print(
                    f"request {index + 1} returned unexpected status "
                    f"{status_code}",
                    file=sys.stderr,
                )
                break
    finally:
        if owns_session:
            client.close()

    return RateLimitResult(admitted, rate_limited, unexpected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        type=_http_url,
        default=os.environ.get("BENCHMARK_URL", DEFAULT_URL),
        help="live API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("BT_API_TOKEN"),
        help="API token; defaults to BT_API_TOKEN",
    )
    parser.add_argument(
        "--cookie",
        default=os.environ.get("BT_RATE_LIMIT_TEST_COOKIE"),
        help="CWA Cookie header; defaults to BT_RATE_LIMIT_TEST_COOKIE",
    )
    parser.add_argument(
        "--requests",
        type=_positive_int,
        default=os.environ.get("BT_RATE_LIMIT_TEST_REQUESTS", "130"),
        help="maximum probes; must exceed the configured per-minute limit",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=os.environ.get("BT_RATE_LIMIT_TEST_TIMEOUT", "5"),
        help="per-request timeout in seconds",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.token and args.cookie:
        parser.error("--token and --cookie are mutually exclusive")
    result = exercise_rate_limit(
        args.url,
        token=args.token,
        request_count=args.requests,
        timeout=args.timeout,
        cookie=args.cookie,
    )
    print(
        f"admitted={result.admitted} "
        f"rate_limited={result.rate_limited} "
        f"unexpected={result.unexpected}"
    )
    if result.admitted < 1 or result.rate_limited < 1 or result.unexpected:
        print(
            "rate-limit probe failed; use a fresh server, valid authentication, "
            "and a request count above BT_RATE_LIMIT_PER_MINUTE",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
