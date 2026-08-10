"""Provider registry, privacy policy, and failover contracts."""

from __future__ import annotations

import unittest
from unittest import mock

import translator
from work_budget import WorkBudget


def _budget() -> WorkBudget:
    return WorkBudget(
        max_attempts=20,
        max_input_bytes=1_000_000,
        max_output_tokens=100_000,
        deadline_seconds=5,
    )


class ProviderConfigurationTests(unittest.TestCase):
    def test_named_gemini_endpoint_is_fixed_and_remote(self):
        provider = translator._provider_from_config(
            name="gemini",
            model="gemini-3.5-flash-lite",
            api_key="gemini-key",
            custom_endpoint="",
            custom_api_key="",
        )

        self.assertEqual(
            provider.url,
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        self.assertEqual(provider.locality, "remote")
        self.assertEqual(provider.cache_namespace, "gemini")

    def test_openai_compatible_uses_dedicated_endpoint_key_and_cache_identity(self):
        public_dns = [
            (translator._socket.AF_INET, translator._socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ]
        with mock.patch.object(translator._socket, "getaddrinfo", return_value=public_dns):
            first = translator._provider_from_config(
                name="openai-compatible",
                model="translation-model",
                api_key="",
                custom_endpoint="https://one.example.test/v1/chat/completions",
                custom_api_key="first-private-key",
            )
            second = translator._provider_from_config(
                name="openai-compatible",
                model="translation-model",
                api_key="",
                custom_endpoint="https://two.example.test/v1/chat/completions",
                custom_api_key="second-private-key",
            )

        self.assertEqual(first.url, "https://one.example.test/v1/chat/completions")
        self.assertEqual(first.api_key, "first-private-key")
        self.assertEqual(first.locality, "remote")
        self.assertNotEqual(first.cache_namespace, second.cache_namespace)
        self.assertNotIn(first.url, first.cache_namespace)

    def test_custom_endpoint_requires_https_exact_path_and_dedicated_key(self):
        invalid = (
            ("http://remote.example.test/v1/chat/completions", "key"),
            ("https://remote.example.test/v1/chat/completions/", "key"),
            ("https://remote.example.test/v1/messages", "key"),
            ("https://remote.example.test/v1/chat/completions", ""),
        )
        for endpoint, key in invalid:
            with self.subTest(endpoint=endpoint, has_key=bool(key)):
                with self.assertRaises(ValueError):
                    translator._provider_from_config(
                        name="openai-compatible",
                        model="translation-model",
                        api_key="",
                        custom_endpoint=endpoint,
                        custom_api_key=key,
                    )

        private_dns = [
            (translator._socket.AF_INET, translator._socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with (
            mock.patch.object(translator._socket, "getaddrinfo", return_value=private_dns),
            self.assertRaises(ValueError),
        ):
            translator._provider_from_config(
                name="openai-compatible",
                model="translation-model",
                api_key="",
                custom_endpoint="https://private.example.test/v1/chat/completions",
                custom_api_key="key",
            )

    def test_custom_transport_connects_to_vetted_ip_not_second_dns_answer(self):
        public_dns = [
            (translator._socket.AF_INET, translator._socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ]
        private_dns = [
            (translator._socket.AF_INET, translator._socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with mock.patch.object(
            translator._socket,
            "getaddrinfo",
            side_effect=[public_dns, private_dns],
        ) as resolver:
            _endpoint, hostname, addresses = translator._resolve_custom_endpoint(
                "https://rebind.example.test/v1/chat/completions"
            )
            translator._HTTP_CALL_CONTEXT.pinned_host = hostname
            translator._HTTP_CALL_CONTEXT.pinned_addresses = addresses
            sentinel_socket = object()
            try:
                connection = translator._DeadlineHTTPSConnection(
                    hostname, 443, timeout=1
                )
                with mock.patch.object(
                    translator._urllib3_connection,
                    "create_connection",
                    return_value=sentinel_socket,
                ) as connect:
                    result = connection._new_conn()
            finally:
                translator._HTTP_CALL_CONTEXT.__dict__.pop("pinned_host", None)
                translator._HTTP_CALL_CONTEXT.__dict__.pop(
                    "pinned_addresses", None
                )

        self.assertIs(result, sentinel_socket)
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 443))
        self.assertEqual(resolver.call_count, 1)

    def test_transient_custom_dns_failure_uses_local_fallback(self):
        primary = translator._Provider(
            "openai-compatible",
            "custom-model",
            "custom-key",
            spec=translator.ProviderSpec(
                provider_id="openai-compatible",
                endpoint="https://unstable.example.test/v1/chat/completions",
                protocol="openai",
                locality="remote",
                cache_namespace="openai-compatible:test",
            ),
        )
        fallback = translator._Provider("local", "gemma4-12b", "")

        class LocalResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "local result"}}]}

            def close(self):
                return None

        calls = []

        def local_only(provider_url, **_kwargs):
            calls.append(provider_url)
            return LocalResponse()

        with (
            mock.patch.object(translator, "_get_primary", return_value=primary),
            mock.patch.object(translator, "_get_fallback", return_value=fallback),
            mock.patch.object(
                translator,
                "_bounded_getaddrinfo",
                side_effect=translator._CustomEndpointDNSFailure("synthetic"),
            ),
            mock.patch.object(translator, "_provider_post", side_effect=local_only),
            mock.patch.object(translator.time, "sleep", return_value=None),
        ):
            translated, backend = translator._complete(
                "private text", "system", max_retries=1, budget=_budget()
            )

        self.assertEqual((translated, backend), ("local result", "local"))
        self.assertEqual(calls, [translator.PROVIDER_ENDPOINTS["local"][0]])

    def test_custom_dns_private_answer_is_terminal_without_fallback(self):
        primary = translator._Provider(
            "openai-compatible",
            "custom-model",
            "custom-key",
            spec=translator.ProviderSpec(
                provider_id="openai-compatible",
                endpoint="https://rebind.example.test/v1/chat/completions",
                protocol="openai",
                locality="remote",
                cache_namespace="openai-compatible:test",
            ),
        )
        fallback = translator._Provider("local", "gemma4-12b", "")
        private_dns = [
            (translator._socket.AF_INET, translator._socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))
        ]
        with (
            mock.patch.object(translator, "_get_primary", return_value=primary),
            mock.patch.object(translator, "_get_fallback", return_value=fallback),
            mock.patch.object(
                translator._socket, "getaddrinfo", return_value=private_dns
            ),
            mock.patch.object(translator, "_provider_post") as provider_post,
            self.assertRaises(translator.ProviderUnavailableError),
        ):
            translator._complete(
                "private text", "system", max_retries=1, budget=_budget()
            )

        provider_post.assert_not_called()

    def test_terminal_primary_4xx_never_fails_over(self):
        primary = translator._Provider("gemini", "gemini-3.5-flash-lite", "key")
        fallback = translator._Provider("local", "gemma4-12b", "")
        calls = []

        def fail(provider, *_args, **_kwargs):
            calls.append(provider.name)
            raise translator._ProviderCallError(provider.name, 401, "HTTPError")

        with (
            mock.patch.object(translator, "_get_primary", return_value=primary),
            mock.patch.object(translator, "_get_fallback", return_value=fallback),
            mock.patch.object(translator, "_call_provider", side_effect=fail),
            self.assertRaises(translator.ProviderUnavailableError),
        ):
            translator._complete("private text", "system", budget=_budget())

        self.assertEqual(calls, ["gemini"])

    def test_transient_primary_failure_uses_local_fallback_without_consent(self):
        primary = translator._Provider("gemini", "gemini-3.5-flash-lite", "key")
        fallback = translator._Provider("local", "gemma4-12b", "")
        calls = []

        def complete(provider, *_args, **_kwargs):
            calls.append(provider.name)
            if provider.name == "gemini":
                raise translator._ProviderCallError(provider.name, 503, "HTTPError")
            return "translated"

        with (
            mock.patch.object(translator, "_get_primary", return_value=primary),
            mock.patch.object(translator, "_get_fallback", return_value=fallback),
            mock.patch.object(translator, "_call_provider", side_effect=complete),
        ):
            translated, backend = translator._complete(
                "private text", "system", budget=_budget()
            )

        self.assertEqual((translated, backend), ("translated", "local"))
        self.assertEqual(calls, ["gemini", "local"])

    def test_provider_policy_exposes_only_locality(self):
        primary = translator._Provider("gemini", "gemini-3.5-flash-lite", "key")
        fallback = translator._Provider("local", "gemma4-12b", "")
        with (
            mock.patch.object(translator, "_get_primary", return_value=primary),
            mock.patch.object(translator, "_get_fallback", return_value=fallback),
        ):
            policy = translator.provider_policy()

        self.assertEqual(policy, {"primary": "remote", "fallback": "local"})
        encoded = repr(policy)
        self.assertNotIn("gemini", encoded)
        self.assertNotIn("gemma", encoded)
        self.assertNotIn("http", encoded)
        self.assertNotIn("key", encoded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
