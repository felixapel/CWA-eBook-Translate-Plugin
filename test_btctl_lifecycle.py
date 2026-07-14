"""Lifecycle contracts for diagnosis, conservative removal, and v2.1.4 migration."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from btctl_compose import ComposeInstaller, InstallError
from btctl_core import DeploymentPlan, InstallConfig, ReleaseIdentity, StateStore
from btctl_lifecycle import (
    DeploymentDoctor,
    LegacyUpgrade,
    MigrationJournalStore,
    RuntimeUninstaller,
)
from test_btctl_compose import FakeDocker, values


class LifecycleDocker(FakeDocker):
    def stop_container(self, name):
        self.calls.append(("stop_container", name))
        self.containers[name]["State"]["Status"] = "exited"

    def start_container(self, name):
        self.calls.append(("start_container", name))
        self.containers[name]["State"] = {"Status": "running"}

    def remove_container(self, name):
        self.calls.append(("remove_container", name))
        self.containers.pop(name, None)

    def remove_network(self, name):
        self.calls.append(("remove_network", name))
        self.networks.pop(name, None)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="9" * 40, clean=True
        )

    def installed(self, root: Path):
        config = InstallConfig.from_mapping(values(root), self.identity)
        plan = DeploymentPlan.from_config(config)
        docker = LifecycleDocker()
        state = ComposeInstaller(docker).install(config, plan, root)
        docker.networks[plan.resources["private_network"]["name"]]["Internal"] = True
        return config, plan, docker, state

    def test_doctor_proves_runtime_identity_auth_networks_ports_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, plan, docker, _ = self.installed(root)

            report = DeploymentDoctor(docker).run(config, plan)

            self.assertTrue(report.ok, report.to_dict())
            self.assertTrue(all(check["status"] == "ok" for check in report.checks))

            api = docker.containers[plan.resources["api"]["name"]]
            api["Config"]["Env"] = [
                item for item in api["Config"]["Env"]
                if not item.startswith("BT_AUTH_MODE=")
            ] + ["BT_AUTH_MODE=disabled", "BT_ALLOW_INSECURE_AUTH=true"]

            drift = DeploymentDoctor(docker).run(config, plan)
            self.assertFalse(drift.ok)
            self.assertIn("runtime", " ".join(str(check) for check in drift.checks))

    def test_uninstall_removes_only_owned_runtime_and_preserves_data_and_cwa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, plan, docker, _ = self.installed(root)
            docker.containers["unrelated"] = {"Id": "preserve-me"}

            state = RuntimeUninstaller(docker).uninstall(config, plan)

            self.assertEqual(state.status, "uninstalled")
            self.assertNotIn(plan.resources["api"]["name"], docker.containers)
            self.assertNotIn(plan.resources["proxy"]["name"], docker.containers)
            self.assertNotIn(plan.resources["private_network"]["name"], docker.networks)
            self.assertIn("calibre-web-automated", docker.containers)
            self.assertIn("unrelated", docker.containers)
            self.assertTrue(Path(config.data_dir).is_dir())
            self.assertTrue(Path(config.backup_dir).parent.is_dir())
            self.assertEqual(StateStore(Path(config.state_dir)).load(), state)

            before = len(docker.calls)
            repeated = RuntimeUninstaller(docker).uninstall(config, plan)
            self.assertEqual(repeated.status, "uninstalled")
            self.assertEqual(len(docker.calls), before)

    def test_uninstall_stops_before_mutation_when_ownership_has_drifted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, plan, docker, _ = self.installed(root)
            docker.containers[plan.resources["proxy"]["name"]]["Id"] = "replacement"
            before = len(docker.calls)

            with self.assertRaisesRegex(InstallError, "ownership"):
                RuntimeUninstaller(docker).uninstall(config, plan)

            self.assertFalse(
                {"remove_container", "remove_network"}
                & {call[0] for call in docker.calls[before:]}
            )

    def test_upgrade_snapshots_offline_then_rollback_restarts_exact_v214(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            legacy_data = root / "legacy-data"
            legacy_data.mkdir()
            database = sqlite3.connect(legacy_data / "translations.db")
            database.execute("CREATE TABLE translations (key TEXT PRIMARY KEY, value TEXT)")
            database.execute("INSERT INTO translations VALUES ('key', 'value')")
            database.commit()
            database.close()
            docker = LifecycleDocker()
            docker.containers["book-translator-v214-rollback"] = {
                "Id": "legacy-container-id",
                "Image": "sha256:legacy-image-id",
                "State": {"Status": "running"},
                "Config": {"Image": "local/book-translator:2.1.4", "Labels": {}},
                "Mounts": [{
                    "Type": "bind",
                    "Source": str(legacy_data),
                    "Destination": "/app/data",
                    "RW": True,
                }],
            }
            migration_values = {
                "BT_LEGACY_CONTAINER": "book-translator-v214-rollback",
                "BT_LEGACY_DATA_DIR": str(legacy_data),
            }
            repository = root / "checkout"
            repository.mkdir()

            state = LegacyUpgrade(docker).upgrade(
                config, plan, repository, migration_values
            )

            self.assertEqual(state.status, "installed")
            self.assertEqual(
                docker.containers["book-translator-v214-rollback"]["State"]["Status"],
                "exited",
            )
            journal = MigrationJournalStore(Path(config.state_dir)).load()
            self.assertEqual(journal["status"], "upgraded")
            snapshot = Path(journal["snapshot_path"])
            self.assertTrue((snapshot / "translations.db").is_file())
            self.assertTrue((Path(config.data_dir) / "translations.db").is_file())

            rolled_back = LegacyUpgrade(docker).rollback(config, plan)

            self.assertEqual(rolled_back.status, "rolled_back")
            self.assertEqual(
                docker.containers["book-translator-v214-rollback"]["State"]["Status"],
                "running",
            )
            self.assertTrue(snapshot.is_dir())
            self.assertTrue(Path(config.data_dir).is_dir())
            self.assertEqual(
                MigrationJournalStore(Path(config.state_dir)).load()["status"],
                "rolled_back",
            )

    def test_upgrade_integrity_failure_restarts_legacy_and_never_builds_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            legacy_data = root / "legacy-data"
            legacy_data.mkdir()
            (legacy_data / "translations.db").write_bytes(b"not a sqlite database")
            repository = root / "checkout"
            repository.mkdir()
            docker = LifecycleDocker()
            docker.containers["book-translator-v214-rollback"] = {
                "Id": "legacy-container-id",
                "Image": "sha256:legacy-image-id",
                "State": {"Status": "running"},
                "Config": {"Image": "local/book-translator:2.1.4", "Labels": {}},
                "Mounts": [{
                    "Type": "bind",
                    "Source": str(legacy_data),
                    "Destination": "/app/data",
                    "RW": True,
                }],
            }

            with self.assertRaisesRegex(InstallError, "integrity"):
                LegacyUpgrade(docker).upgrade(config, plan, repository, {
                    "BT_LEGACY_CONTAINER": "book-translator-v214-rollback",
                    "BT_LEGACY_DATA_DIR": str(legacy_data),
                })

            self.assertEqual(
                docker.containers["book-translator-v214-rollback"]["State"]["Status"],
                "running",
            )
            self.assertNotIn("build_image", [call[0] for call in docker.calls])
            journal = MigrationJournalStore(Path(config.state_dir)).load()
            self.assertEqual(journal["status"], "upgrade-failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
