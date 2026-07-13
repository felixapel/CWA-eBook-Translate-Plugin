"""Authentication boundary contracts: fail closed, bounded, and tenant-safe."""

from __future__ import annotations

import threading
import time
import os
import unittest
from unittest import mock

import requests

from auth import (
    AuthConfigError,
    AuthRejected,
    AuthUnavailable,
    RequestAuthenticator,
)

os.environ.setdefault("BT_AUTH_MODE", "disabled")
os.environ.setdefault("BT_ALLOW_INSECURE_AUTH", "true")

import server


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        content_type="application/json",
        body=b"[]",
        content_length=None,
    ):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class AuthenticationConfigTests(unittest.TestCase):
    def test_default_mode_is_fail_closed_token_auth(self):
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({})

    def test_disabled_mode_must_be_explicit(self):
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({"BT_AUTH_MODE": "disabled"})
        auth = RequestAuthenticator.from_environment({
            "BT_AUTH_MODE": "disabled",
            "BT_ALLOW_INSECURE_AUTH": "true",
        })
        identity = auth.authenticate({}, "127.0.0.1")
        self.assertEqual(identity.subject, "legacy-anonymous")
        self.assertEqual(auth.mode, "disabled")

    def test_invalid_mode_and_cwa_url_are_rejected_at_startup(self):
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({"BT_AUTH_MODE": "magic"})
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({
                "BT_AUTH_MODE": "cwa_session",
                "BT_CWA_AUTH_URL": "file:///etc/passwd",
            })
        for invalid_url in (
            "http://calibre-web:bad/ajax/emailstat",
            "http://calibre-web:8083/ping",
            "http://calibre-web:8083/ajax/emailstat?public=1",
        ):
            with self.subTest(invalid_url=invalid_url), self.assertRaises(AuthConfigError):
                RequestAuthenticator.from_environment({
                    "BT_AUTH_MODE": "cwa_session",
                    "BT_CWA_AUTH_URL": invalid_url,
                })
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({
                "BT_AUTH_MODE": "cwa_session",
                "BT_CWA_AUTH_URL": "http://calibre-web:8083/ajax/emailstat",
                "BT_CWA_AUTH_MAX_RESPONSE_BYTES": "0",
            })
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({
                "BT_AUTH_MODE": "cwa_session",
                "BT_CWA_AUTH_URL": "http://calibre-web:8083/ajax/emailstat",
                "BT_API_TOKEN": "unsafe operator token",
            })
        with self.assertRaises(AuthConfigError):
            RequestAuthenticator.from_environment({
                "BT_AUTH_MODE": "forwarded",
                "BT_IDENTITY_TRUSTED_PROXIES": "",
            })

    def test_cors_configuration_accepts_only_exact_origins(self):
        self.assertEqual(
            server._validate_cors_origin("https://books.example.test:8443"),
            "https://books.example.test:8443",
        )
        for origin in (
            "*",
            "https://books.example.test/path",
            "https://user@books.example.test",
            "https://books.example.test?query=1",
            "https://books.example.test:bad",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                server._validate_cors_origin(origin)


class TokenAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.auth = RequestAuthenticator(mode="token", api_token="correct-horse")

    def test_missing_and_wrong_tokens_are_rejected(self):
        with self.assertRaises(AuthRejected):
            self.auth.authenticate({}, "127.0.0.1")
        with self.assertRaises(AuthRejected):
            self.auth.authenticate({"X-BT-Token": "wrong"}, "127.0.0.1")
        with self.assertRaises(AuthRejected):
            self.auth.authenticate({"X-BT-Token": "snowman-☃"}, "127.0.0.1")

    def test_configured_token_must_be_an_ascii_header_value(self):
        for invalid in ("contains space", "snowman-☃", "line\nbreak"):
            with self.subTest(invalid=invalid), self.assertRaises(AuthConfigError):
                RequestAuthenticator(mode="token", api_token=invalid)
        self.assertFalse(server._token_matches("snowman-☃", "operator-token"))

    def test_token_identity_is_opaque(self):
        identity = self.auth.authenticate(
            {"X-BT-Token": "correct-horse"}, "127.0.0.1"
        )
        self.assertTrue(identity.subject.startswith("token:"))
        self.assertNotIn("correct-horse", identity.subject)
        self.assertEqual(identity.roles, frozenset({"operator"}))


class ForwardedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.auth = RequestAuthenticator(
            mode="forwarded",
            identity_trusted_proxies=("10.42.0.0/16", "127.0.0.1/32"),
        )

    def test_direct_client_cannot_forge_identity_headers(self):
        with self.assertRaises(AuthRejected):
            self.auth.authenticate(
                {"X-BT-Subject": "admin", "X-BT-Roles": "admin"},
                "192.168.1.77",
            )

    def test_trusted_proxy_sets_opaque_subject_and_bounded_roles(self):
        identity = self.auth.authenticate(
            {"X-BT-Subject": "user@example.test", "X-BT-Roles": "reader, admin"},
            "10.42.8.9",
        )
        self.assertTrue(identity.subject.startswith("forwarded:"))
        self.assertNotIn("user@example.test", identity.subject)
        self.assertEqual(identity.roles, frozenset({"reader", "admin"}))

    def test_missing_or_malformed_subject_and_roles_are_rejected(self):
        for headers in (
            {},
            {"X-BT-Subject": "x\nspoof"},
            {"X-BT-Subject": "user", "X-BT-Roles": "reader,not a role"},
        ):
            with self.subTest(headers=headers), self.assertRaises(AuthRejected):
                self.auth.authenticate(headers, "127.0.0.1")


class CWASessionAuthenticationTests(unittest.TestCase):
    def make_auth(self, get, **overrides):
        return RequestAuthenticator(
            mode="cwa_session",
            cwa_auth_url="http://calibre-web:8083/ajax/emailstat",
            cwa_cookie_names=("session", "remember_token"),
            cwa_timeout_seconds=overrides.get("cwa_timeout_seconds", 0.5),
            cwa_cache_ttl_seconds=10,
            cwa_cache_max_entries=2,
            cwa_max_inflight=4,
            cwa_max_response_bytes=overrides.get("cwa_max_response_bytes", 1024),
            http_get=get,
        )

    def test_valid_session_forwards_only_selected_cookies_without_redirects(self):
        response = FakeResponse()
        get = mock.Mock(return_value=response)
        auth = self.make_auth(get)

        identity = auth.authenticate(
            {"Cookie": "ignored=private; session=session-secret; remember_token=remember"},
            "172.18.0.4",
        )

        self.assertTrue(identity.subject.startswith("cwa-session:"))
        self.assertNotIn("session-secret", identity.subject)
        get.assert_called_once_with(
            "http://calibre-web:8083/ajax/emailstat",
            headers={
                "Accept": "application/json",
                "Cookie": "session=session-secret; remember_token=remember",
            },
            timeout=0.5,
            allow_redirects=False,
            stream=True,
        )
        self.assertTrue(response.closed)

    def test_success_must_match_the_cwa_task_list_contract(self):
        invalid_responses = (
            FakeResponse(body=b'{"authenticated": true}'),
            FakeResponse(body=b'["not-a-task-object"]'),
            FakeResponse(body=b"not-json"),
            FakeResponse(content_type="text/html", body=b"[]"),
        )
        for response in invalid_responses:
            with self.subTest(body=response.body, content_type=response.headers["Content-Type"]):
                auth = self.make_auth(mock.Mock(return_value=response))
                with self.assertRaises(AuthRejected):
                    auth.authenticate({"Cookie": "session=wrong-endpoint"}, "127.0.0.1")

    def test_success_body_is_bounded_before_json_parsing(self):
        response = FakeResponse(body=b"[{}]", content_length=2048)
        auth = self.make_auth(
            mock.Mock(return_value=response), cwa_max_response_bytes=1024
        )
        with self.assertRaises(AuthUnavailable):
            auth.authenticate({"Cookie": "session=oversized"}, "127.0.0.1")
        self.assertTrue(response.closed)

        response = FakeResponse(body=b"[" + (b" " * 1024) + b"]")
        auth = self.make_auth(
            mock.Mock(return_value=response), cwa_max_response_bytes=1024
        )
        with self.assertRaises(AuthUnavailable):
            auth.authenticate({"Cookie": "session=streamed-oversized"}, "127.0.0.1")
        self.assertTrue(response.closed)

    def test_streamed_body_has_an_absolute_deadline(self):
        class SlowDripResponse(FakeResponse):
            def iter_content(self, chunk_size=8192):
                del chunk_size
                yield b"["
                time.sleep(0.05)
                yield b"]"

        response = SlowDripResponse()
        auth = self.make_auth(
            mock.Mock(return_value=response), cwa_timeout_seconds=0.01
        )

        with self.assertRaises(AuthUnavailable):
            auth.authenticate({"Cookie": "session=slow-drip"}, "127.0.0.1")

        self.assertTrue(response.closed)

    def test_missing_cookie_and_login_redirect_are_unauthorized(self):
        get = mock.Mock(return_value=FakeResponse(status_code=302, content_type="text/html"))
        auth = self.make_auth(get)
        with self.assertRaises(AuthRejected):
            auth.authenticate({}, "127.0.0.1")
        get.assert_not_called()
        with self.assertRaises(AuthRejected):
            auth.authenticate({"Cookie": "session=expired"}, "127.0.0.1")

    def test_upstream_failures_are_sanitized_as_unavailable(self):
        def failed(*_args, **_kwargs):
            raise requests.exceptions.ConnectionError("private upstream detail")

        auth = self.make_auth(failed)
        with self.assertRaises(AuthUnavailable) as captured:
            auth.authenticate({"Cookie": "session=secret"}, "127.0.0.1")
        self.assertNotIn("private upstream detail", str(captured.exception))

        auth = self.make_auth(lambda *_args, **_kwargs: FakeResponse(status_code=500))
        with self.assertRaises(AuthUnavailable):
            auth.authenticate({"Cookie": "session=secret"}, "127.0.0.1")

    def test_validation_is_cached_but_distinct_sessions_stay_isolated(self):
        get = mock.Mock(return_value=FakeResponse())
        auth = self.make_auth(get)
        first = auth.authenticate({"Cookie": "session=one"}, "127.0.0.1")
        repeat = auth.authenticate({"Cookie": "session=one"}, "127.0.0.1")
        second = auth.authenticate({"Cookie": "session=two"}, "127.0.0.1")
        self.assertEqual(first.subject, repeat.subject)
        self.assertNotEqual(first.subject, second.subject)
        self.assertEqual(get.call_count, 2)

        auth.authenticate({"Cookie": "session=three"}, "127.0.0.1")
        self.assertLessEqual(auth.cache_entries, 2)

    def test_default_transport_ignores_environment_proxies_and_closes_session(self):
        response = FakeResponse()
        fake_session = mock.Mock()
        fake_session.trust_env = True
        fake_session.get.return_value = response
        auth = self.make_auth(None)

        with mock.patch.object(requests, "Session", return_value=fake_session):
            identity = auth.authenticate(
                {"Cookie": "session=direct-only"}, "127.0.0.1"
            )

        self.assertTrue(identity.subject.startswith("cwa-session:"))
        self.assertFalse(fake_session.trust_env)
        fake_session.get.assert_called_once()
        fake_session.close.assert_called_once()
        self.assertTrue(response.closed)

    def test_concurrent_validation_is_coalesced(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_get(*_args, **_kwargs):
            calls.append(1)
            entered.set()
            release.wait(2)
            return FakeResponse()

        auth = self.make_auth(slow_get)
        identities = []
        errors = []

        def validate():
            try:
                identities.append(
                    auth.authenticate({"Cookie": "session=same"}, "127.0.0.1")
                )
            except Exception as exc:  # pragma: no cover - assertion evidence
                errors.append(exc)

        threads = [threading.Thread(target=validate) for _ in range(8)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(1))
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(2)

        self.assertFalse(errors)
        self.assertEqual(len(identities), 8)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len({identity.subject for identity in identities}), 1)


class ServerAuthenticationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_authenticator = server.AUTHENTICATOR
        self.original_auth_limit = server.BT_AUTH_RATE_LIMIT_PER_MINUTE
        self.original_rate_client_cap = server.BT_RATE_LIMIT_MAX_CLIENTS
        self.original_origins = server.ALLOWED_ORIGINS
        self.original_allow_private = server.BT_ALLOW_PRIVATE_LAN
        server._auth_rate_limit_store.clear()
        server._rate_limit_store.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server.AUTHENTICATOR = self.original_authenticator
        server.BT_AUTH_RATE_LIMIT_PER_MINUTE = self.original_auth_limit
        server.BT_RATE_LIMIT_MAX_CLIENTS = self.original_rate_client_cap
        server.ALLOWED_ORIGINS = self.original_origins
        server.BT_ALLOW_PRIVATE_LAN = self.original_allow_private
        server._auth_rate_limit_store.clear()
        server._rate_limit_store.clear()

    def test_protected_route_rejects_before_translation_or_cache_work(self):
        server.AUTHENTICATOR = RequestAuthenticator(
            mode="token", api_token="integration-secret"
        )
        with mock.patch.object(server, "_cache_lookup") as cache_lookup, mock.patch.object(
            server, "translate_text"
        ) as translate_text:
            response = self.client.post("/translate", json={"text": "private text"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "unauthorized")
        cache_lookup.assert_not_called()
        translate_text.assert_not_called()

    def test_authenticated_subject_owns_cache_namespace(self):
        authenticator = RequestAuthenticator(
            mode="token", api_token="integration-secret"
        )
        server.AUTHENTICATOR = authenticator
        expected_subject = authenticator.authenticate(
            {"X-BT-Token": "integration-secret"}, "127.0.0.1"
        ).subject

        with mock.patch.object(server, "_cache_lookup", return_value="hola") as lookup:
            response = self.client.post(
                "/translate",
                headers={"X-BT-Token": "integration-secret"},
                json={
                    "text": "hello",
                    "book_id": "book-1",
                    "chapter_id": "chapter-2",
                    "tenant": "client-spoof",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lookup.call_args.kwargs["tenant"], expected_subject)
        self.assertNotEqual(lookup.call_args.kwargs["tenant"], "client-spoof")

    def test_shallow_health_is_independent_of_auth_authority(self):
        unavailable = mock.Mock(mode="cwa_session")
        unavailable.authenticate.side_effect = AuthUnavailable(
            "private authority detail"
        )
        server.AUTHENTICATOR = unavailable

        self.assertEqual(self.client.get("/health").status_code, 200)
        protected = self.client.get("/metrics")
        self.assertEqual(protected.status_code, 503)
        self.assertEqual(protected.get_json()["error"], "authentication_unavailable")
        self.assertNotIn("private authority detail", protected.get_data(as_text=True))

    def test_authentication_attempts_have_a_separate_rate_limit(self):
        server.AUTHENTICATOR = RequestAuthenticator(
            mode="token", api_token="integration-secret"
        )
        server.BT_AUTH_RATE_LIMIT_PER_MINUTE = 1

        first = self.client.get("/metrics", environ_base={"REMOTE_ADDR": "198.51.100.9"})
        second = self.client.get("/metrics", environ_base={"REMOTE_ADDR": "198.51.100.9"})
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["error"], "rate_limited")

    def test_auth_rate_limit_identity_map_is_bounded_fail_closed(self):
        server.AUTHENTICATOR = RequestAuthenticator(
            mode="token", api_token="integration-secret"
        )
        server.BT_RATE_LIMIT_MAX_CLIENTS = 1

        first = self.client.get("/metrics", environ_base={"REMOTE_ADDR": "198.51.100.10"})
        new_identity = self.client.get(
            "/metrics", environ_base={"REMOTE_ADDR": "198.51.100.11"}
        )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(new_identity.status_code, 429)
        self.assertLessEqual(len(server._auth_rate_limit_store), 1)

    def test_cwa_cookie_cors_requires_an_exact_origin(self):
        server.AUTHENTICATOR = RequestAuthenticator(
            mode="cwa_session",
            cwa_auth_url="http://calibre-web:8083/ajax/emailstat",
            http_get=lambda *_args, **_kwargs: FakeResponse(),
        )
        server.ALLOWED_ORIGINS = {"https://books.example.test"}
        server.BT_ALLOW_PRIVATE_LAN = True

        exact = self.client.get(
            "/ping", headers={"Origin": "https://books.example.test"}
        )
        broad_private = self.client.get(
            "/ping", headers={"Origin": "http://192.168.1.10:8083"}
        )
        self.assertEqual(
            exact.headers.get("Access-Control-Allow-Origin"),
            "https://books.example.test",
        )
        self.assertEqual(exact.headers.get("Access-Control-Allow-Credentials"), "true")
        self.assertIn("Origin", exact.headers.get("Vary", ""))
        self.assertIsNone(broad_private.headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
