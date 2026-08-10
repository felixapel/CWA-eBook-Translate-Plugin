"""Transactional provider-only runtime reconfiguration contracts."""

from __future__ import annotations

import tempfile
import unittest
import copy
import json
import hashlib
from pathlib import Path

from btctl_core import ConfigError, DeploymentPlan, InstallConfig, ReleaseIdentity, StateStore
from btctl_reconfigure import ProviderReconfigurer
from btctl_unraid import InstallError, UnraidInstaller
from btctl_unraid import _environment_text, _write_private
from tests.python.test_btctl_unraid import FakeDocker, values
from btctl_compose import ComposeInstaller
from tests.python.test_btctl_compose import FakeDocker as ComposeFakeDocker
from tests.python.test_btctl_compose import values as compose_values


class ReconfigureDocker(FakeDocker):
    def __init__(self, *, fail_first_provider_probe: bool = False):
        super().__init__()
        self.fail_first_provider_probe = fail_first_provider_probe
        self.provider_probes = 0

    def probe_providers(self, container):
        self.calls.append(("probe_providers", container))
        self.provider_probes += 1
        if self.fail_first_provider_probe and self.provider_probes == 1:
            raise InstallError("configured provider probe failed")


class ReconfigureComposeDocker(ComposeFakeDocker):
    def probe_providers(self, container):
        self.calls.append(("probe_providers", container))

    def compose_recreate_service(self, document, project, service):
        self.calls.append(("compose_recreate_service", str(document), project, service))
        payload = json.loads(Path(document).read_text(encoding="utf-8"))
        proxy_name = payload["services"]["proxy"]["container_name"]
        proxy = copy.deepcopy(self.containers[proxy_name])
        self.compose_up(document, project)
        self.containers[proxy_name] = proxy


class CleanupFailReconfigurer(ProviderReconfigurer):
    def __init__(self, docker):
        super().__init__(docker)
        self.fail_cleanup = True

    def _cleanup(self, paths):
        if self.fail_cleanup:
            self.fail_cleanup = False
            raise OSError("synthetic crash after state commit")
        super()._cleanup(paths)


class ProviderReconfigureTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.3.0-rc.1", sha="e" * 40, clean=True
        )

    def installed(self, root: Path, docker: ReconfigureDocker):
        old_values = values(root, reader="kavita")
        old_values.update(
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "gpt-4.1-mini",
                "LLM_API_KEY": "old-openai-secret",
                "BT_LOCAL_URL": "",
            }
        )
        old_config = InstallConfig.from_mapping(old_values, self.identity)
        old_plan = DeploymentPlan.from_config(old_config)
        def prepare(path: Path):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)

        state = UnraidInstaller(docker, prepare_data=prepare).install(
            old_config, old_plan, root
        )
        return old_values, old_config, old_plan, state

    def new_config(self, old_values: dict[str, str]):
        new_values = {
            **old_values,
            "LLM_PROVIDER": "gemini",
            "LLM_MODEL": "gemini-3.5-flash-lite",
            "LLM_API_KEY": "new-gemini-secret",
            "LLM_FALLBACK_PROVIDER": "local",
            "LLM_FALLBACK_MODEL": "gemma4-12b",
            "BT_LOCAL_URL": "http://host.docker.internal:2819/v1/chat/completions",
        }
        config = InstallConfig.from_mapping(new_values, self.identity)
        return config, DeploymentPlan.from_config(config)

    def rotated_key_config(self, old_values: dict[str, str]):
        new_values = {**old_values, "LLM_API_KEY": "rotated-openai-secret"}
        config = InstallConfig.from_mapping(new_values, self.identity)
        return config, DeploymentPlan.from_config(config)

    @staticmethod
    def seed_key_rotation_journal(reconfigurer, config, plan, old_state, status):
        (
            _state,
            api,
            _proxy,
            _current,
            target,
            paths,
        ) = reconfigurer._load_current(config, plan)
        old_text = paths["active"].read_text(encoding="utf-8")
        new_text = _environment_text(target)
        _write_private(paths["old"], old_text)
        _write_private(paths["new"], new_text)
        reconfigurer._save_journal(
            paths["journal"],
            {
                "schema_version": 1,
                "status": status,
                "install_id": old_state.install_id,
                "old_config_fingerprint": old_state.config_fingerprint,
                "new_config_fingerprint": plan.config_fingerprint,
                "old_snapshot_sha256": hashlib.sha256(
                    old_text.encode("utf-8")
                ).hexdigest(),
                "new_snapshot_sha256": hashlib.sha256(
                    new_text.encode("utf-8")
                ).hexdigest(),
                "role": "api",
                "credentials_changed": True,
                "custom_endpoint_changed": False,
                "old_api_id": api["Id"],
                "new_api_id": None,
            },
        )
        return paths

    def test_preview_is_provider_only_and_never_discloses_credentials_or_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, _state = self.installed(root, docker)
            config, plan = self.new_config(old_values)

            preview = ProviderReconfigurer(docker).preview(config, plan)

            encoded = repr(preview)
            self.assertEqual(preview["role"], "api")
            self.assertEqual(preview["from"]["provider"], "openai")
            self.assertEqual(preview["to"]["provider"], "gemini")
            self.assertNotIn("old-openai-secret", encoded)
            self.assertNotIn("new-gemini-secret", encoded)
            self.assertNotIn("host.docker.internal", encoded)

            changed = {**old_values, "BT_PROXY_PORT": "8399"}
            invalid = InstallConfig.from_mapping(changed, self.identity)
            with self.assertRaisesRegex(ConfigError, "provider-only"):
                ProviderReconfigurer(docker).preview(
                    invalid, DeploymentPlan.from_config(invalid)
                )

    def test_unraid_reconfigure_replaces_only_api_and_commits_new_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.new_config(old_values)
            proxy_name = str(plan.resources["proxy"]["name"])
            proxy_id = docker.containers[proxy_name]["Id"]

            state = ProviderReconfigurer(docker).reconfigure(config, plan)

            self.assertEqual(state.install_id, old_state.install_id)
            self.assertEqual(state.config_fingerprint, plan.config_fingerprint)
            self.assertEqual(docker.containers[proxy_name]["Id"], proxy_id)
            api_env = (Path(config.state_dir) / "api.env").read_text(encoding="utf-8")
            self.assertIn("LLM_PROVIDER=gemini", api_env)
            self.assertIn("LLM_FALLBACK_PROVIDER=local", api_env)
            self.assertNotIn("old-openai-secret", api_env)
            self.assertGreaterEqual(docker.provider_probes, 1)
            for filename in (
                "reconfigure.json",
                "api.env.reconfigure-old",
                "api.env.reconfigure-new",
            ):
                self.assertFalse((Path(config.state_dir) / filename).exists())

    def test_credential_only_rotation_uses_private_state_not_public_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.rotated_key_config(old_values)

            self.assertEqual(old_state.config_fingerprint, plan.config_fingerprint)
            state = ProviderReconfigurer(docker).reconfigure(config, plan)

            self.assertEqual(state.config_fingerprint, old_state.config_fingerprint)
            api_env = (Path(config.state_dir) / "api.env").read_text(encoding="utf-8")
            self.assertIn("rotated-openai-secret", api_env)
            self.assertNotIn("old-openai-secret", api_env)

    def test_prepared_key_rotation_crash_rolls_back_then_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.rotated_key_config(old_values)
            reconfigurer = ProviderReconfigurer(docker)
            paths = self.seed_key_rotation_journal(
                reconfigurer, config, plan, old_state, "prepared"
            )

            state = reconfigurer.reconfigure(config, plan)

            self.assertEqual(state.config_fingerprint, old_state.config_fingerprint)
            self.assertIn(
                "rotated-openai-secret",
                paths["active"].read_text(encoding="utf-8"),
            )
            self.assertFalse(paths["journal"].exists())

    def test_key_rotation_crash_after_api_removal_rolls_back_then_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.rotated_key_config(old_values)
            reconfigurer = ProviderReconfigurer(docker)
            paths = self.seed_key_rotation_journal(
                reconfigurer, config, plan, old_state, "replacing"
            )
            reconfigurer._remove_owned_api(
                config, old_state, str(plan.resources["api"]["name"])
            )

            state = reconfigurer.reconfigure(config, plan)

            self.assertEqual(state.config_fingerprint, old_state.config_fingerprint)
            self.assertIn(
                "rotated-openai-secret",
                paths["active"].read_text(encoding="utf-8"),
            )
            self.assertFalse(paths["journal"].exists())

    def test_key_rotation_crash_after_state_commit_finishes_new_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.rotated_key_config(old_values)

            with self.assertRaisesRegex(InstallError, "committed"):
                CleanupFailReconfigurer(docker).reconfigure(config, plan)

            state = ProviderReconfigurer(docker).reconfigure(config, plan)
            self.assertEqual(state.config_fingerprint, old_state.config_fingerprint)
            self.assertIn(
                "rotated-openai-secret",
                (Path(config.state_dir) / "api.env").read_text(encoding="utf-8"),
            )
            self.assertFalse(
                (Path(config.state_dir) / "reconfigure.json").exists()
            )

    def test_interrupted_cutover_is_rolled_back_before_a_fresh_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker()
            old_values, _old_config, _old_plan, old_state = self.installed(root, docker)
            config, plan = self.new_config(old_values)
            reconfigurer = ProviderReconfigurer(docker)
            (
                _state,
                _api,
                _proxy,
                _current,
                target,
                paths,
            ) = reconfigurer._load_current(config, plan)
            old_text = paths["active"].read_text(encoding="utf-8")
            new_text = _environment_text(target)
            _write_private(paths["old"], old_text)
            _write_private(paths["new"], new_text)
            _write_private(paths["active"], new_text)
            reconfigurer._save_journal(
                paths["journal"],
                {
                    "schema_version": 1,
                    "status": "replacing",
                    "install_id": old_state.install_id,
                    "old_config_fingerprint": old_state.config_fingerprint,
                    "new_config_fingerprint": plan.config_fingerprint,
                    "old_snapshot_sha256": hashlib.sha256(
                        old_text.encode("utf-8")
                    ).hexdigest(),
                    "new_snapshot_sha256": hashlib.sha256(
                        new_text.encode("utf-8")
                    ).hexdigest(),
                    "role": "api",
                    "credentials_changed": True,
                    "custom_endpoint_changed": False,
                    "old_api_id": old_state.resources["api"]["id"],
                    "new_api_id": None,
                },
            )

            state = reconfigurer.reconfigure(config, plan)

            self.assertEqual(state.config_fingerprint, plan.config_fingerprint)
            self.assertGreaterEqual(docker.provider_probes, 2)
            self.assertIn(
                "LLM_PROVIDER=gemini",
                paths["active"].read_text(encoding="utf-8"),
            )
            self.assertFalse(paths["journal"].exists())

    def test_failed_new_provider_probe_restores_old_runtime_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = ReconfigureDocker(fail_first_provider_probe=True)
            old_values, old_config, old_plan, old_state = self.installed(root, docker)
            config, plan = self.new_config(old_values)
            proxy_name = str(plan.resources["proxy"]["name"])
            proxy_id = docker.containers[proxy_name]["Id"]

            with self.assertRaisesRegex(InstallError, "rolled back"):
                ProviderReconfigurer(docker).reconfigure(config, plan)

            restored = StateStore(Path(config.state_dir)).load()
            self.assertEqual(restored.config_fingerprint, old_state.config_fingerprint)
            self.assertEqual(restored.install_id, old_state.install_id)
            self.assertEqual(docker.containers[proxy_name]["Id"], proxy_id)
            api_env = (Path(config.state_dir) / "api.env").read_text(encoding="utf-8")
            self.assertIn("LLM_PROVIDER=openai", api_env)
            self.assertIn("old-openai-secret", api_env)
            self.assertNotIn("new-gemini-secret", api_env)
            self.assertGreaterEqual(docker.provider_probes, 2)
            self.assertEqual(old_config.public_contract(), old_config.public_contract())
            self.assertEqual(old_plan.config_fingerprint, restored.config_fingerprint)

    def test_compose_reconfigure_recreates_only_api_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_values = compose_values(root, reader="kavita")
            old_config = InstallConfig.from_mapping(old_values, self.identity)
            old_plan = DeploymentPlan.from_config(old_config)
            docker = ReconfigureComposeDocker()
            old_state = ComposeInstaller(docker).install(old_config, old_plan, root)
            new_values = {
                **old_values,
                "LLM_PROVIDER": "gemini",
                "LLM_MODEL": "gemini-3.5-flash-lite",
                "LLM_API_KEY": "compose-gemini-secret",
                "LLM_FALLBACK_PROVIDER": "local",
                "LLM_FALLBACK_MODEL": "gemma4-12b",
            }
            config = InstallConfig.from_mapping(new_values, self.identity)
            plan = DeploymentPlan.from_config(config)
            proxy_name = str(plan.resources["proxy"]["name"])
            proxy_id = docker.containers[proxy_name]["Id"]

            state = ProviderReconfigurer(docker).reconfigure(config, plan)

            self.assertEqual(state.install_id, old_state.install_id)
            self.assertEqual(state.config_fingerprint, plan.config_fingerprint)
            self.assertEqual(docker.containers[proxy_name]["Id"], proxy_id)
            self.assertIn(
                "compose-gemini-secret",
                (Path(config.state_dir) / "deployment.compose.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                ("probe_providers", str(plan.resources["api"]["name"])),
                docker.calls,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
