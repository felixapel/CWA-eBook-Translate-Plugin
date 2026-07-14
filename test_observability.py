"""Bounded observability contracts for every production failure boundary."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("BT_AUTH_MODE", "disabled")
os.environ.setdefault("BT_ALLOW_INSECURE_AUTH", "true")

from auth import AuthUnavailable, RequestAuthenticator
import server
from translator import ProviderUnavailableError
from work_budget import WorkBudgetExceeded


class ObservabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.original_authenticator = server.AUTHENTICATOR
        self.original_auth_limit = server.BT_AUTH_RATE_LIMIT_PER_MINUTE
        self.original_rate_limit = server.RATE_LIMIT_MAX
        server.AUTHENTICATOR = RequestAuthenticator(mode="disabled")
        server._auth_rate_limit_store.clear()
        server._rate_limit_store.clear()
        server._reset_metrics_for_tests()
        self.client = server.app.test_client()

    def tearDown(self):
        server.AUTHENTICATOR = self.original_authenticator
        server.BT_AUTH_RATE_LIMIT_PER_MINUTE = self.original_auth_limit
        server.RATE_LIMIT_MAX = self.original_rate_limit
        server._auth_rate_limit_store.clear()
        server._rate_limit_store.clear()
        server._reset_metrics_for_tests()

    def metrics(self, *, remote_addr: str = "127.0.0.1") -> dict:
        response = self.client.get(
            "/metrics",
            headers={"X-BT-Token": "observability-secret"},
            environ_base={"REMOTE_ADDR": remote_addr},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_auth_rejection_and_auth_rate_limit_are_counted(self):
        server.AUTHENTICATOR = RequestAuthenticator(
            mode="token", api_token="observability-secret"
        )
        server.BT_AUTH_RATE_LIMIT_PER_MINUTE = 1

        first = self.client.get(
            "/metrics", environ_base={"REMOTE_ADDR": "198.51.100.10"}
        )
        second = self.client.get(
            "/metrics", environ_base={"REMOTE_ADDR": "198.51.100.10"}
        )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        snapshot = self.metrics(remote_addr="198.51.100.11")
        self.assertEqual(snapshot["http_responses"]["4xx"], 2)
        self.assertEqual(snapshot["outcomes"]["auth_rejected"], 1)
        self.assertEqual(snapshot["outcomes"]["auth_rate_limited"], 1)

    def test_auth_authority_outage_is_counted(self):
        unavailable = mock.Mock(mode="cwa_session")
        unavailable.authenticate.side_effect = AuthUnavailable("private detail")
        server.AUTHENTICATOR = unavailable

        failed = self.client.get(
            "/metrics", environ_base={"REMOTE_ADDR": "198.51.100.12"}
        )
        self.assertEqual(failed.status_code, 503)

        server.AUTHENTICATOR = RequestAuthenticator(
            mode="token", api_token="observability-secret"
        )
        snapshot = self.metrics(remote_addr="198.51.100.13")
        self.assertEqual(snapshot["http_responses"]["5xx"], 1)
        self.assertEqual(snapshot["outcomes"]["auth_unavailable"], 1)

    def test_api_rate_limit_and_validation_failure_are_counted(self):
        server.RATE_LIMIT_MAX = 1

        accepted = self.client.post(
            "/translate",
            json={"text": "hello", "source_lang": "English", "target_lang": "English"},
            environ_base={"REMOTE_ADDR": "198.51.100.20"},
        )
        limited = self.client.post(
            "/translate",
            json={"text": "again", "source_lang": "English", "target_lang": "English"},
            environ_base={"REMOTE_ADDR": "198.51.100.20"},
        )
        # Disabled development auth intentionally has one shared subject.
        # Start a fresh API window so this assertion exercises validation,
        # not the already-proven subject quota above.
        server._rate_limit_store.clear()
        invalid = self.client.post(
            "/translate",
            json={"text": 17},
            environ_base={"REMOTE_ADDR": "198.51.100.21"},
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(invalid.status_code, 400)
        snapshot = self.metrics(remote_addr="198.51.100.22")
        self.assertEqual(snapshot["http_responses"]["2xx"], 1)
        self.assertEqual(snapshot["http_responses"]["4xx"], 2)
        self.assertEqual(snapshot["outcomes"]["api_rate_limited"], 1)

    def test_deadline_provider_and_partial_batch_failures_are_counted(self):
        with (
            mock.patch.object(server, "_cache_lookup", return_value=None),
            mock.patch.object(
                server,
                "translate_text",
                side_effect=WorkBudgetExceeded("deadline"),
            ),
        ):
            deadline = self.client.post("/translate", json={"text": "deadline"})

        with (
            mock.patch.object(server, "_cache_lookup", return_value=None),
            mock.patch.object(
                server,
                "translate_text",
                side_effect=ProviderUnavailableError("private detail"),
            ),
        ):
            provider = self.client.post("/translate", json={"text": "provider"})

        partial_result = {
            "translations": [
                "translated",
                "[TRANSLATION ERROR: provider_unavailable]",
                "[TRANSLATION ERROR: translation_failed]",
            ],
            "backends": ["local", "unknown", "unknown"],
            "cached": [False, False, False],
            "cached_count": 0,
            "fresh_count": 1,
            "total_elapsed_ms": 7,
        }
        with mock.patch.object(
            server, "_translate_paragraphs", return_value=partial_result
        ):
            partial = self.client.post(
                "/translate/batch", json={"paragraphs": ["one", "two", "three"]}
            )

        self.assertEqual(deadline.status_code, 503)
        self.assertEqual(provider.status_code, 502)
        self.assertEqual(partial.status_code, 200)
        snapshot = self.metrics(remote_addr="198.51.100.30")
        self.assertEqual(snapshot["http_responses"]["5xx"], 2)
        self.assertEqual(snapshot["outcomes"]["work_budget_exhausted"], 1)
        self.assertEqual(snapshot["work_budget_reasons"]["deadline"], 1)
        self.assertEqual(snapshot["outcomes"]["provider_unavailable"], 1)
        self.assertEqual(snapshot["outcomes"]["batch_partial_failure_requests"], 1)
        self.assertEqual(snapshot["batch_partial_failure_segments"], 2)

    def test_metric_dimensions_are_fixed_and_reject_dynamic_labels(self):
        snapshot = self.metrics()

        self.assertEqual(
            set(snapshot["http_responses"]), {"2xx", "3xx", "4xx", "5xx"}
        )
        self.assertEqual(
            set(snapshot["work_budget_reasons"]),
            {"attempts", "input_bytes", "output_tokens", "deadline", "queue", "cancelled", "unknown"},
        )
        self.assertEqual(
            set(snapshot["outcomes"]),
            {
                "auth_rejected",
                "auth_unavailable",
                "auth_rate_limited",
                "api_rate_limited",
                "work_budget_exhausted",
                "provider_unavailable",
                "invalid_provider_response",
                "translation_failed",
                "internal_error",
                "batch_partial_failure_requests",
            },
        )
        with self.assertRaises(ValueError):
            server._record_outcome("book-title-controlled-label")


if __name__ == "__main__":
    unittest.main(verbosity=2)
