import contextlib
import io
import unittest
from unittest import mock

import benchmark
import benchmark_realistic
import test_ratelimit


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {"translation": "ok", "elapsed_ms": 10}
        self.closed = False

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, statuses, payloads=None):
        self._statuses = iter(statuses)
        self._payloads = iter(payloads or [])
        self.calls = []
        self.responses = []
        self.closed = False
        self.trust_env = True

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        try:
            payload = next(self._payloads)
        except StopIteration:
            payload = None
        response = _FakeResponse(next(self._statuses), payload)
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


class RateLimitLiveScriptTests(unittest.TestCase):
    def test_stops_after_first_429_without_provider_work(self):
        session = _FakeSession([200, 200, 429, 200])

        result = test_ratelimit.exercise_rate_limit(
            "http://127.0.0.1:8390",
            token="secret",
            request_count=10,
            timeout=3.5,
            cookie=None,
            session=session,
        )

        self.assertEqual(result, test_ratelimit.RateLimitResult(2, 1, 0))
        self.assertEqual(len(session.calls), 3)
        for index, (url, kwargs) in enumerate(session.calls):
            self.assertEqual(url, "http://127.0.0.1:8390/translate")
            self.assertEqual(kwargs["timeout"], 3.5)
            self.assertTrue(kwargs["stream"])
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["headers"], {"X-BT-Token": "secret"})
            self.assertEqual(kwargs["json"]["text"], f"rate-limit-probe-{index}")
            self.assertEqual(kwargs["json"]["source_lang"], "English")
            self.assertEqual(kwargs["json"]["target_lang"], "English")
        self.assertTrue(all(response.closed for response in session.responses))

    def test_unexpected_status_is_counted_without_response_body(self):
        session = _FakeSession([401, 200, 429])

        with contextlib.redirect_stderr(io.StringIO()):
            result = test_ratelimit.exercise_rate_limit(
                "http://example.invalid/",
                token=None,
                request_count=3,
                timeout=1,
                cookie=None,
                session=session,
            )

        self.assertEqual(result, test_ratelimit.RateLimitResult(0, 0, 1))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["headers"], {})
        self.assertTrue(session.responses[0].closed)

    def test_owned_session_ignores_environment_proxies_and_is_closed(self):
        session = _FakeSession([200, 429])

        with mock.patch.object(test_ratelimit.requests, "Session", return_value=session):
            result = test_ratelimit.exercise_rate_limit(
                "https://translator.example/bt-api",
                token="secret",
                request_count=2,
                timeout=1,
            )

        self.assertEqual(result, test_ratelimit.RateLimitResult(1, 1, 0))
        self.assertFalse(session.trust_env)
        self.assertTrue(session.closed)
        self.assertTrue(all(response.closed for response in session.responses))

    def test_redirect_is_not_followed_with_authentication_headers(self):
        session = _FakeSession([302, 200, 429])

        with contextlib.redirect_stderr(io.StringIO()):
            result = test_ratelimit.exercise_rate_limit(
                "https://translator.example/bt-api",
                token=None,
                cookie="session=opaque-value",
                request_count=3,
                timeout=1,
                session=session,
            )

        self.assertEqual(result, test_ratelimit.RateLimitResult(0, 0, 1))
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.responses[0].closed)

    def test_supports_cwa_session_cookie_without_a_token(self):
        session = _FakeSession([200, 429])

        result = test_ratelimit.exercise_rate_limit(
            "http://127.0.0.1:8080/bt-api",
            token=None,
            cookie="session=opaque-value",
            request_count=2,
            timeout=1,
            session=session,
        )

        self.assertEqual(result, test_ratelimit.RateLimitResult(1, 1, 0))
        self.assertEqual(
            session.calls[0][1]["headers"],
            {"Cookie": "session=opaque-value"},
        )

    def test_main_fails_when_no_request_is_admitted_or_limited(self):
        stderr = io.StringIO()
        with mock.patch.object(
            test_ratelimit,
            "exercise_rate_limit",
            return_value=test_ratelimit.RateLimitResult(0, 0, 3),
        ), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(
            io.StringIO()
        ):
            result = test_ratelimit.main([
                "--url", "http://127.0.0.1:8390",
                "--requests", "3",
            ])

        self.assertEqual(result, 1)
        self.assertIn("failed", stderr.getvalue())

    def test_main_rejects_nonpositive_numeric_arguments(self):
        for args in (("--requests", "0"), ("--timeout", "nan")):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    test_ratelimit.main(list(args))
            self.assertEqual(raised.exception.code, 2)

    def test_main_rejects_ambiguous_authentication(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                test_ratelimit.main([
                    "--token", "token-value",
                    "--cookie", "session=cookie-value",
                ])
        self.assertEqual(raised.exception.code, 2)

    def test_main_rejects_non_http_or_ambiguous_urls(self):
        invalid_urls = (
            "ftp://translator.example/bt-api",
            "https://user:password@translator.example/bt-api",
            "https://translator.example/bt-api?target=elsewhere",
            "https://translator.example/bt-api#fragment",
            "https:///bt-api",
        )
        for url in invalid_urls:
            with self.subTest(url=url), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    test_ratelimit.main(["--url", url])
                self.assertEqual(raised.exception.code, 2)


class BenchmarkLiveScriptTests(unittest.TestCase):
    def test_quick_benchmark_uses_token_and_refuses_redirects(self):
        session = _FakeSession([200])

        result = benchmark.make_request(
            7,
            base_url="https://books.example.test/bt-api",
            token="opaque-token",
            cookie=None,
            timeout=9,
            session=session,
        )

        self.assertEqual(result["translation"], "ok")
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://books.example.test/bt-api/translate")
        self.assertEqual(kwargs["headers"], {"X-BT-Token": "opaque-token"})
        self.assertEqual(kwargs["timeout"], 9)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(session.responses[0].closed)

    def test_realistic_benchmark_supports_cwa_cookie(self):
        session = _FakeSession([200], [{"translations": ["hola"]}])

        result, _elapsed = benchmark_realistic.translate_batch(
            ["hello"],
            base_url="https://books.example.test/bt-api/",
            token=None,
            cookie="session=opaque-value",
            timeout=13,
            session=session,
        )

        self.assertEqual(result, {"translations": ["hola"]})
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://books.example.test/bt-api/translate/batch")
        self.assertEqual(kwargs["headers"], {"Cookie": "session=opaque-value"})
        self.assertEqual(kwargs["timeout"], 13)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(session.responses[0].closed)

    def test_benchmarks_fail_closed_on_non_2xx(self):
        for module, callable_, arguments in (
            (
                benchmark,
                benchmark.make_request,
                (1,),
            ),
            (
                benchmark_realistic,
                benchmark_realistic.translate_batch,
                (["hello"],),
            ),
        ):
            with self.subTest(module=module.__name__):
                session = _FakeSession([302])
                with self.assertRaisesRegex(RuntimeError, "unexpected HTTP 302"):
                    callable_(
                        *arguments,
                        base_url="https://books.example.test/bt-api",
                        token=None,
                        cookie="session=opaque-value",
                        timeout=5,
                        session=session,
                    )
                self.assertFalse(session.calls[0][1]["allow_redirects"])
                self.assertTrue(session.responses[0].closed)

    def test_benchmark_cli_rejects_ambiguous_auth_and_url_credentials(self):
        for module in (benchmark, benchmark_realistic):
            with self.subTest(module=module.__name__):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        module.main([
                            "--url", "https://user:password@books.example.test/bt-api",
                        ])
                self.assertEqual(raised.exception.code, 2)

                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        module.main(["--token", "x", "--cookie", "session=y"])
                self.assertEqual(raised.exception.code, 2)

    def test_owned_benchmark_sessions_ignore_environment_proxies(self):
        for module, runner in (
            (benchmark, benchmark.run_benchmark),
            (benchmark_realistic, benchmark_realistic.run_benchmark_scenario),
        ):
            with self.subTest(module=module.__name__):
                session = _FakeSession([200] * 4, [{"translations": ["ok"]}] * 4)
                with mock.patch.object(module.requests, "Session", return_value=session):
                    if module is benchmark:
                        runner(
                            1,
                            1,
                            base_url="http://127.0.0.1:8390",
                            token="secret",
                            cookie=None,
                            timeout=3,
                        )
                    else:
                        runner(
                            "test",
                            1,
                            1,
                            1,
                            base_url="http://127.0.0.1:8390",
                            token="secret",
                            cookie=None,
                            timeout=3,
                        )
                self.assertFalse(session.trust_env)
                self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
