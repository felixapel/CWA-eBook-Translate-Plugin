import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from btctl_core import (
    ConfigError,
    DeploymentPlan,
    DeploymentState,
    InstallConfig,
    ReleaseIdentity,
    StateStore,
    compatibility_tier,
    parse_env_text,
    redact_mapping,
)


ROOT = Path(__file__).parent
BTCTL = ROOT / "btctl"


class ReleaseIdentityTests(unittest.TestCase):
    def test_clean_checkout_derives_immutable_local_image(self):
        identity = ReleaseIdentity.from_checkout(
            version="2.2.0",
            sha="0123456789abcdef0123456789abcdef01234567",
            clean=True,
        )

        self.assertEqual(identity.image, "local/cwa-translate:2.2.0-0123456789ab")

    def test_dirty_or_non_semver_checkout_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "clean checkout"):
            ReleaseIdentity.from_checkout(version="2.2.0", sha="a" * 40, clean=False)
        with self.assertRaisesRegex(ConfigError, "VERSION"):
            ReleaseIdentity.from_checkout(version="latest", sha="a" * 40, clean=True)


class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="b" * 40, clean=True
        )
        self.base = {
            "BT_INSTALL_PROFILE": "compose-existing",
            "BT_INGRESS_MODE": "published",
            "BT_AUTH_PROFILE": "cwa-session",
            "BT_PUBLIC_ORIGIN": "https://books.example.test",
            "CWA_UPSTREAM": "http://calibre-web-automated:8083",
            "BT_CWA_CONTAINER": "calibre-web-automated",
            "BT_CWA_NETWORK": "cwa_default",
            "BT_CWA_VERSION": "4.0.6",
            "BT_STATE_DIR": "/srv/cwa-translate/state",
            "BT_DATA_DIR": "/srv/cwa-translate/data",
            "BT_BACKUP_DIR": "/srv/cwa-translate/backups",
            "BT_PROXY_PORT": "8385",
            "LLM_PROVIDER": "local",
            "LLM_MODEL": "gemma4-12b",
            "BT_LOCAL_URL": "http://host.docker.internal:2819/v1/chat/completions",
        }

    def test_cwa_session_profile_derives_safe_runtime_contract(self):
        config = InstallConfig.from_mapping(self.base, self.identity)
        api = config.api_environment()
        proxy = config.proxy_environment()

        self.assertEqual(api["BT_AUTH_MODE"], "cwa_session")
        self.assertEqual(proxy["BT_BROWSER_AUTH_MODE"], "cwa_session")
        self.assertEqual(proxy["BT_BROWSER_CREDENTIALS"], "same-origin")
        self.assertEqual(
            api["BT_CWA_AUTH_URL"],
            "http://calibre-web-automated:8083/ajax/emailstat",
        )
        self.assertEqual(config.image, self.identity.image)
        self.assertNotIn("CWA_UPSTREAM", api)
        self.assertNotIn("LLM_API_KEY", proxy)
        self.assertNotIn("BT_INSTALL_PROFILE", api)
        self.assertNotIn("BT_IMAGE", api)
        self.assertNotIn("BT_ALLOW_INSECURE_AUTH", api)

    def test_forwarded_profile_requires_exact_peer_and_patched_authentik(self):
        values = {
            **self.base,
            "BT_INGRESS_MODE": "docker-edge",
            "BT_EDGE_NETWORK": "authentik_backend",
            "BT_PROXY_PORT": "",
            "BT_AUTH_PROFILE": "authentik-forwarded",
            "BT_IDENTITY_PROXY_IP": "172.30.50.9/32",
            "BT_AUTHENTIK_VERSION": "2025.12.4",
            "BT_REVERSE_PROXY": "nginx",
        }

        config = InstallConfig.from_mapping(values, self.identity)
        api = config.api_environment()
        proxy = config.proxy_environment()

        self.assertEqual(api["BT_AUTH_MODE"], "forwarded")
        self.assertEqual(proxy["BT_BROWSER_AUTH_MODE"], "forwarded")
        self.assertEqual(proxy["BT_BROWSER_CREDENTIALS"], "include")
        self.assertEqual(api["BT_IDENTITY_TRUSTED_PROXIES"], "172.30.50.9/32")
        self.assertEqual(api["BT_FORWARDED_SUBJECT_HEADER"], "X-authentik-uid")
        self.assertNotIn("BT_CWA_AUTH_URL", api)

    def test_forwarded_profile_rejects_broad_peer_and_affected_versions(self):
        for peer in ("172.30.50.0/24", "10.0.0.0/8", "0.0.0.0/0"):
            with self.subTest(peer=peer), self.assertRaisesRegex(
                ConfigError, "exact /32 or /128"
            ):
                InstallConfig.from_mapping(
                    {
                        **self.base,
                        "BT_INGRESS_MODE": "docker-edge",
                        "BT_EDGE_NETWORK": "authentik_backend",
                        "BT_PROXY_PORT": "",
                        "BT_AUTH_PROFILE": "authentik-forwarded",
                        "BT_IDENTITY_PROXY_IP": peer,
                        "BT_AUTHENTIK_VERSION": "2025.12.4",
                        "BT_REVERSE_PROXY": "traefik",
                    },
                    self.identity,
                )

        with self.assertRaisesRegex(ConfigError, "CVE-2026-25748"):
            InstallConfig.from_mapping(
                {
                    **self.base,
                    "BT_INGRESS_MODE": "docker-edge",
                    "BT_EDGE_NETWORK": "authentik_backend",
                    "BT_PROXY_PORT": "",
                    "BT_AUTH_PROFILE": "authentik-forwarded",
                    "BT_IDENTITY_PROXY_IP": "2001:db8::9/128",
                    "BT_AUTHENTIK_VERSION": "2025.10.3",
                    "BT_REVERSE_PROXY": "caddy",
                },
                self.identity,
            )

    def test_cwa_compatibility_is_explicit_and_fail_closed(self):
        self.assertEqual(compatibility_tier("4.0.6"), "tier1")
        self.assertEqual(compatibility_tier("4.9.0"), "tier1")
        self.assertEqual(compatibility_tier("3.1.4"), "legacy")
        for version in ("3.1.3", "3.2.0", "5.0.0", "latest"):
            with self.subTest(version=version), self.assertRaises(ConfigError):
                compatibility_tier(version)

    def test_profile_specific_network_and_path_contract(self):
        with self.assertRaisesRegex(ConfigError, "BT_EDGE_NETWORK"):
            InstallConfig.from_mapping(
                {
                    **self.base,
                    "BT_INGRESS_MODE": "docker-edge",
                    "BT_PROXY_PORT": "",
                    "BT_AUTH_PROFILE": "authentik-forwarded",
                    "BT_IDENTITY_PROXY_IP": "172.30.50.9/32",
                    "BT_AUTHENTIK_VERSION": "2025.12.4",
                    "BT_REVERSE_PROXY": "nginx",
                },
                self.identity,
            )
        with self.assertRaisesRegex(ConfigError, "absolute"):
            InstallConfig.from_mapping(
                {**self.base, "BT_STATE_DIR": "./state"}, self.identity
            )
        with self.assertRaisesRegex(ConfigError, "unsafe for DockerMan"):
            InstallConfig.from_mapping(
                {
                    **self.base,
                    "BT_INSTALL_PROFILE": "unraid",
                    "BT_STATE_DIR": "/mnt/user/appdata/cwa;--privileged",
                    "BT_UNRAID_TEMPLATE_DIR": "/boot/config/plugins/dockerMan/templates-user",
                },
                self.identity,
            )

    def test_install_contract_never_accepts_disabled_or_shared_token_auth(self):
        for profile in ("disabled", "token", "forwarded"):
            with self.subTest(profile=profile), self.assertRaisesRegex(
                ConfigError, "BT_AUTH_PROFILE"
            ):
                InstallConfig.from_mapping(
                    {**self.base, "BT_AUTH_PROFILE": profile}, self.identity
                )
        with self.assertRaisesRegex(ConfigError, "BT_INSTALL_PROFILE"):
            InstallConfig.from_mapping(
                {**self.base, "BT_INSTALL_PROFILE": "compose-bundled"}, self.identity
            )

    def test_provider_contract_requires_exactly_one_credential_path(self):
        with self.assertRaisesRegex(ConfigError, "BT_LOCAL_URL"):
            InstallConfig.from_mapping(
                {**self.base, "BT_LOCAL_URL": "", "LLM_API_KEY": "secret"},
                self.identity,
            )
        with self.assertRaisesRegex(ConfigError, "LLM_API_KEY"):
            InstallConfig.from_mapping(
                {
                    **self.base,
                    "LLM_PROVIDER": "openai",
                    "BT_LOCAL_URL": "",
                    "LLM_API_KEY": "",
                },
                self.identity,
            )

    def test_redaction_never_returns_secret_values(self):
        values = {
            "LLM_API_KEY": "cloud-secret",
            "BT_API_TOKEN": "compat-secret",
            "BT_PUBLIC_ORIGIN": "https://books.example.test",
        }

        redacted = redact_mapping(values)

        self.assertEqual(redacted["LLM_API_KEY"], "<redacted>")
        self.assertEqual(redacted["BT_API_TOKEN"], "<redacted>")
        self.assertEqual(
            redacted["BT_PUBLIC_ORIGIN"], "https://books.example.test"
        )
        self.assertNotIn("cloud-secret", repr(redacted))
        self.assertNotIn("compat-secret", repr(redacted))


class PlanAndStateTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="c" * 40, clean=True
        )
        self.values = {
            "BT_INSTALL_PROFILE": "compose-existing",
            "BT_INGRESS_MODE": "published",
            "BT_AUTH_PROFILE": "cwa-session",
            "BT_PUBLIC_ORIGIN": "https://books.example.test",
            "CWA_UPSTREAM": "http://calibre-web-automated:8083",
            "BT_CWA_CONTAINER": "calibre-web-automated",
            "BT_CWA_NETWORK": "cwa_default",
            "BT_CWA_VERSION": "4.0.6",
            "BT_STATE_DIR": "/srv/cwa-translate/state",
            "BT_DATA_DIR": "/srv/cwa-translate/data",
            "BT_BACKUP_DIR": "/srv/cwa-translate/backups",
            "BT_PROXY_PORT": "8385",
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "gpt-4.1-mini",
            "BT_LOCAL_URL": "",
            "LLM_API_KEY": "never-print-this",
        }

    def test_plan_is_deterministic_redacted_and_declares_exact_ownership(self):
        config = InstallConfig.from_mapping(self.values, self.identity)

        first = DeploymentPlan.from_config(config).to_dict()
        second = DeploymentPlan.from_config(config).to_dict()

        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        self.assertNotIn("never-print-this", encoded)
        self.assertEqual(first["image"], self.identity.image)
        self.assertEqual(first["compatibility_tier"], "tier1")
        self.assertEqual(first["resources"]["cwa"]["ownership"], "external")
        self.assertEqual(first["resources"]["api"]["ownership"], "owned")
        self.assertEqual(first["resources"]["proxy"]["ownership"], "owned")
        self.assertEqual(first["resources"]["api"]["published_ports"], [])
        self.assertEqual(first["resources"]["proxy"]["published_ports"], [8385])

    def test_state_is_private_atomic_schema_versioned_and_secret_free(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            state = DeploymentState.new(
                install_id="01234567-89ab-4cde-8123-0123456789ab",
                plan=DeploymentPlan.from_config(
                    InstallConfig.from_mapping(self.values, self.identity)
                ),
            )
            store.save(state)

            state_file = Path(directory) / "state.json"
            payload = state_file.read_text(encoding="utf-8")
            self.assertEqual(os.stat(state_file).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            self.assertNotIn("never-print-this", payload)
            self.assertEqual(store.load(), state)

    def test_state_rejects_unknown_schema_and_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text('{"schema_version": 999}')
            with self.assertRaisesRegex(ConfigError, "schema"):
                StateStore(root).load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "elsewhere"
            target.write_text("preserve")
            (root / "state.json").symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "symbolic link"):
                StateStore(root).save(
                    DeploymentState.new(
                        install_id="01234567-89ab-4cde-8123-0123456789ab",
                        plan=DeploymentPlan.from_config(
                            InstallConfig.from_mapping(self.values, self.identity)
                        ),
                    )
                )


class CLIPlanTests(unittest.TestCase):
    def test_plan_outputs_json_without_mutating_state_or_leaking_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "btctl test"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "btctl@example.invalid"], cwd=repository, check=True)
            (repository / "VERSION").write_text("2.2.0\n", encoding="utf-8")
            subprocess.run(["git", "add", "VERSION"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repository, check=True)
            state_dir = Path(directory) / "state"
            env_file = Path(directory) / "install.env"
            env_file.write_text(
                "\n".join(
                    (
                        "BT_INSTALL_PROFILE=compose-existing",
                        "BT_INGRESS_MODE=published",
                        "BT_AUTH_PROFILE=cwa-session",
                        "BT_PUBLIC_ORIGIN=https://books.example.test",
                        "CWA_UPSTREAM=http://calibre-web-automated:8083",
                        "BT_CWA_CONTAINER=calibre-web-automated",
                        "BT_CWA_NETWORK=cwa_default",
                        "BT_CWA_VERSION=4.0.6",
                        f"BT_STATE_DIR={state_dir}",
                        f"BT_DATA_DIR={Path(directory) / 'data'}",
                        f"BT_BACKUP_DIR={Path(directory) / 'backups'}",
                        "BT_PROXY_PORT=8385",
                        "LLM_PROVIDER=openai",
                        "LLM_MODEL=gpt-4.1-mini",
                        "BT_LOCAL_URL=",
                        "LLM_API_KEY=top-secret",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(BTCTL), "--repository", str(repository), "plan", "--env", str(env_file), "--json"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["image"].split(":")[0], "local/cwa-translate")
            self.assertNotIn("top-secret", result.stdout + result.stderr)
            self.assertFalse(state_dir.exists())

    def test_invalid_config_uses_exit_64_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "bad.env"
            env_file.write_text("BT_AUTH_PROFILE=disabled\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BTCTL), "plan", "--env", str(env_file)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 64)
            self.assertNotIn("Traceback", result.stderr)

class EnvParserTests(unittest.TestCase):
    def test_parser_accepts_comments_and_rejects_duplicates_or_shell_syntax(self):
        parsed = parse_env_text("# install\nBT_INSTALL_PROFILE=unraid\nLLM_MODEL='gemma 4'\n")
        self.assertEqual(parsed["BT_INSTALL_PROFILE"], "unraid")
        self.assertEqual(parsed["LLM_MODEL"], "gemma 4")

        with self.assertRaisesRegex(ConfigError, "duplicate"):
            parse_env_text("A=1\nA=2\n")
        with self.assertRaisesRegex(ConfigError, "KEY=value"):
            parse_env_text("export A=1\n")
        with self.assertRaisesRegex(ConfigError, "substitution"):
            parse_env_text("A=$(id)\n")


if __name__ == "__main__":
    unittest.main()
