import contextlib
import io
import unittest
from unittest import mock

import test_ratelimit


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeSession:
    def __init__(self, statuses):
        self._statuses = iter(statuses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(next(self._statuses))


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
            self.assertEqual(kwargs["headers"], {"X-BT-Token": "secret"})
            self.assertEqual(kwargs["json"]["text"], f"rate-limit-probe-{index}")
            self.assertEqual(kwargs["json"]["source_lang"], "English")
            self.assertEqual(kwargs["json"]["target_lang"], "English")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
