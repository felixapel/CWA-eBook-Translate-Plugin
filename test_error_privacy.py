"""No provider body, URL, or exception detail may cross public/log boundaries."""
import unittest
import uuid
import threading
from unittest import mock

import requests

import server
import translator


SENTINEL = "PRIVATE-BOOK-TEXT-DO-NOT-LOG"
PRIVATE_URL = "https://private.example/secret-path"


class ErrorPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.original_primary = translator._primary_provider
        self.original_fallback = translator._fallback_provider
        self.original_sleep = translator.time.sleep
        self.original_api_token = server.API_TOKEN
        self.original_health_cache = dict(translator._health_cache)
        self.original_sem = translator._UPSTREAM_SEM
        self.original_queue_timeout = translator.BT_UPSTREAM_QUEUE_TIMEOUT
        translator._primary_provider = translator._Provider(
            "local", "privacy-test-model", "")
        translator._fallback_provider = None
        translator.time.sleep = lambda _seconds: None
        translator._health_cache = {"ts": 0.0, "data": None}
        server.API_TOKEN = ""
        server._rate_limit_store.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        translator._primary_provider = self.original_primary
        translator._fallback_provider = self.original_fallback
        translator.time.sleep = self.original_sleep
        translator._health_cache = self.original_health_cache
        translator._UPSTREAM_SEM = self.original_sem
        translator.BT_UPSTREAM_QUEUE_TIMEOUT = self.original_queue_timeout
        server.API_TOKEN = self.original_api_token
        server._rate_limit_store.clear()

    @staticmethod
    def leaking_http_error(*_args, **_kwargs):
        response = mock.Mock(status_code=502, text=SENTINEL)
        raise requests.exceptions.HTTPError(
            f"{SENTINEL} from {PRIVATE_URL}", response=response)

    def assert_private_details_absent(self, response, logs):
        combined = response.get_data(as_text=True) + "\n" + "\n".join(logs)
        self.assertNotIn(SENTINEL, combined)
        self.assertNotIn(PRIVATE_URL, combined)

    def test_provider_failure_has_stable_sanitized_contract(self):
        with mock.patch.object(
            translator.requests, "post", side_effect=self.leaking_http_error
        ), self.assertLogs(level="WARNING") as captured:
            response = self.client.post(
                "/translate",
                json={"text": f"privacy-miss-{uuid.uuid4()}"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "provider_unavailable")
        self.assert_private_details_absent(response, captured.output)

    def test_deep_health_never_returns_provider_exception_details(self):
        with mock.patch.object(
            translator.requests, "post", side_effect=self.leaking_http_error
        ), mock.patch.object(
            server, "_get_cleanup_token", return_value="operator-token"
        ):
            response = self.client.get(
                "/health/deep",
                headers={"X-BT-Token": "operator-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assert_private_details_absent(response, [])

    def test_shallow_health_never_touches_a_provider(self):
        with mock.patch.object(translator.requests, "post") as provider_call:
            health = self.client.get("/health")
            ready = self.client.get("/ready")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertNotIn("backends", health.get_json())
        provider_call.assert_not_called()

    def test_anonymous_deep_health_is_rejected_before_probe(self):
        with mock.patch.object(
            server, "_get_cleanup_token", return_value="operator-token"
        ), mock.patch.object(server, "check_backend_health") as deep_probe:
            response = self.client.get("/health/deep")

        self.assertEqual(response.status_code, 401)
        deep_probe.assert_not_called()

    def test_deep_health_accepts_configured_api_token_without_cleanup_file(self):
        server.API_TOKEN = "configured-api-token"
        with mock.patch.object(
            server, "_get_cleanup_token",
            side_effect=AssertionError("cleanup credential must not be consulted"),
        ), mock.patch.object(
            server, "check_backend_health", return_value={
                "local (primary)": {
                    "status": "ok", "latency_ms": 1, "error": None,
                },
            },
        ) as deep_probe:
            response = self.client.get(
                "/health/deep",
                headers={"X-BT-Token": "configured-api-token"},
            )

        self.assertEqual(response.status_code, 200)
        deep_probe.assert_called_once()

    def test_deep_health_obeys_the_global_provider_gate(self):
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(1)
        translator.BT_UPSTREAM_QUEUE_TIMEOUT = 0.001
        translator._UPSTREAM_SEM.acquire()
        try:
            with mock.patch.object(
                server, "_get_cleanup_token", return_value="operator-token"
            ), mock.patch.object(translator.requests, "post") as provider_call:
                response = self.client.get(
                    "/health/deep",
                    headers={"X-BT-Token": "operator-token"},
                )
        finally:
            translator._UPSTREAM_SEM.release()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["reason"], "queue")
        provider_call.assert_not_called()

    def test_unexpected_single_error_is_sanitized(self):
        with mock.patch.object(
            server, "translate_text", side_effect=RuntimeError(SENTINEL)
        ), self.assertLogs(level="ERROR") as captured:
            response = self.client.post(
                "/translate",
                json={"text": f"unexpected-miss-{uuid.uuid4()}"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "translation_failed")
        self.assert_private_details_absent(response, captured.output)

    def test_unexpected_batch_error_is_sanitized_json(self):
        with mock.patch.object(
            server, "translate_batch", side_effect=RuntimeError(SENTINEL)
        ), self.assertLogs(level="ERROR") as captured:
            response = self.client.post(
                "/translate/batch",
                json={"paragraphs": [f"batch-miss-{uuid.uuid4()}"]},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "translation_failed")
        self.assert_private_details_absent(response, captured.output)

    def test_unknown_route_and_wrong_method_use_json_error_envelopes(self):
        missing = self.client.get("/definitely-not-a-route")
        wrong_method = self.client.get("/translate")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.content_type, "application/json")
        self.assertEqual(missing.get_json()["error"], "not_found")
        self.assertEqual(wrong_method.status_code, 405)
        self.assertEqual(wrong_method.content_type, "application/json")
        self.assertEqual(wrong_method.get_json()["error"], "method_not_allowed")

    def test_unhandled_framework_error_is_sanitized_json(self):
        failing_view = mock.Mock(side_effect=RuntimeError(SENTINEL))
        with mock.patch.dict(
            server.app.view_functions, {"metrics": failing_view}
        ), self.assertLogs("book-translator.server", level="ERROR") as captured:
            response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["error"], "internal_error")
        self.assert_private_details_absent(response, captured.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
