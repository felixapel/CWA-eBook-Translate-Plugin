"""Universal one-container runtime contracts for CWA and Kavita."""

from __future__ import annotations

import unittest

from hub_runtime import HubConfig, HubConfigError


def dual_reader_environment() -> dict[str, str]:
    return {
        "BT_ENABLE_CWA": "true",
        "BT_ENABLE_KAVITA": "true",
        "BT_CWA_PUBLIC_ORIGIN": "https://books.example.test",
        "BT_CWA_READER_UPSTREAM": "http://calibre-web:8083",
        "BT_CWA_READER_VERSION": "4.0.6",
        "BT_CWA_AUTH_PROFILE": "reader-session",
        "BT_CWA_READER_CONNECTOR_ID": "01234567-89ab-4cde-8123-0123456789ab",
        "BT_CWA_PUBLISHED_PORT": "8385",
        "BT_KAVITA_PUBLIC_ORIGIN": "https://kavita.example.test",
        "BT_KAVITA_READER_UPSTREAM": "http://kavita:5000",
        "BT_KAVITA_READER_VERSION": "0.9.0.2",
        "BT_KAVITA_AUTH_PROFILE": "reader-session",
        "BT_KAVITA_READER_CONNECTOR_ID": "11234567-89ab-4cde-8123-0123456789ab",
        "BT_KAVITA_PUBLISHED_PORT": "8386",
        "LLM_PROVIDER": "gemini",
        "LLM_MODEL": "gemini-3.5-flash-lite",
        "LLM_API_KEY": "shared-secret",
        "BT_KAVITA_LLM_PROVIDER": "local",
        "BT_KAVITA_LLM_MODEL": "gemma4-12b",
        "BT_KAVITA_LLM_API_KEY": "",
        "BT_KAVITA_LOCAL_URL": "http://host.docker.internal:8000/v1/chat/completions",
        "BT_MAX_CONCURRENT": "2",
        "BT_MAX_UPSTREAM_INFLIGHT": "2",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }


class HubConfigTests(unittest.TestCase):
    def test_at_least_one_reader_must_be_enabled(self):
        with self.assertRaisesRegex(HubConfigError, "at least one"):
            HubConfig.from_environment({
                "BT_ENABLE_CWA": "false",
                "BT_ENABLE_KAVITA": "false",
            })

    def test_dual_reader_contract_has_distinct_loopback_and_persistent_state(self):
        config = HubConfig.from_environment(dual_reader_environment())

        self.assertEqual(tuple(reader.name for reader in config.readers), ("cwa", "kavita"))
        cwa, kavita = config.readers
        self.assertEqual(cwa.api_port, 8391)
        self.assertEqual(kavita.api_port, 8392)
        self.assertEqual(cwa.proxy_port, 8080)
        self.assertEqual(kavita.proxy_port, 8081)
        self.assertEqual(cwa.environment["DB_PATH"], "/app/data/cwa/translations.db")
        self.assertEqual(kavita.environment["DB_PATH"], "/app/data/kavita/translations.db")
        self.assertEqual(cwa.environment["BT_SESSION_COOKIE_NAME"], "__Host-bt-cwa-session")
        self.assertEqual(
            kavita.environment["BT_SESSION_COOKIE_NAME"],
            "__Host-bt-kavita-session",
        )

    def test_shared_provider_is_inherited_and_present_empty_override_clears_secret(self):
        config = HubConfig.from_environment(dual_reader_environment())
        cwa, kavita = config.readers

        self.assertEqual(cwa.environment["LLM_PROVIDER"], "gemini")
        self.assertEqual(cwa.environment["LLM_API_KEY"], "shared-secret")
        self.assertEqual(kavita.environment["LLM_PROVIDER"], "local")
        self.assertEqual(kavita.environment["LLM_API_KEY"], "")
        self.assertEqual(kavita.environment["LLM_MODEL"], "gemma4-12b")
        self.assertNotIn("BT_KAVITA_LLM_API_KEY", cwa.environment)
        self.assertNotIn("BT_CWA_LLM_API_KEY", kavita.environment)

    def test_total_concurrency_is_split_without_oversubscription(self):
        config = HubConfig.from_environment(dual_reader_environment())

        self.assertEqual(
            sum(int(reader.environment["BT_MAX_CONCURRENT"]) for reader in config.readers),
            2,
        )
        self.assertEqual(
            sum(
                int(reader.environment["BT_MAX_UPSTREAM_INFLIGHT"])
                for reader in config.readers
            ),
            2,
        )

    def test_published_ports_must_not_collide(self):
        env = dual_reader_environment()
        env["BT_KAVITA_PUBLISHED_PORT"] = env["BT_CWA_PUBLISHED_PORT"]

        with self.assertRaisesRegex(HubConfigError, "published ports"):
            HubConfig.from_environment(env)

    def test_single_reader_receives_the_whole_concurrency_budget(self):
        env = dual_reader_environment()
        env["BT_ENABLE_KAVITA"] = "false"

        config = HubConfig.from_environment(env)

        self.assertEqual(len(config.readers), 1)
        self.assertEqual(config.readers[0].environment["BT_MAX_CONCURRENT"], "2")
        self.assertEqual(
            config.readers[0].environment["BT_MAX_UPSTREAM_INFLIGHT"], "2"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
