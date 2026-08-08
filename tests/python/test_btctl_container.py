from __future__ import annotations

import hashlib
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from btctl_container import (
    ConfigError,
    MountPlan,
    MountSpec,
    command_path_access,
    command_requires_socket,
    create_mount_plan,
    legacy_data_path,
    mount_source_for_path,
    validate_storage_path,
)


class ContainerMountMatrixTests(unittest.TestCase):
    def test_each_command_gets_only_its_required_managed_paths(self):
        expected = {
            "plan": (),
            "auth-snippet": (),
            "doctor": (
                ("state", "ro"),
                ("data", "ro"),
                ("template", "ro"),
            ),
            "adopt": (
                ("state", "rw"),
                ("data", "ro"),
                ("template", "ro"),
            ),
            "install": (
                ("state", "rw"),
                ("data", "rw"),
                ("template", "rw"),
            ),
            "uninstall": (
                ("state", "rw"),
                ("data", "ro"),
                ("template", "rw"),
            ),
            "upgrade": (
                ("state", "rw"),
                ("data", "rw"),
                ("backup", "rw"),
                ("legacy", "rw"),
                ("template", "rw"),
            ),
            "rollback": (
                ("state", "rw"),
                ("data", "ro"),
                ("backup", "ro"),
                ("legacy", "ro"),
                ("template", "rw"),
            ),
        }
        for command, access in expected.items():
            with self.subTest(command=command):
                self.assertEqual(command_path_access(command), access)
                self.assertEqual(
                    command_requires_socket(command),
                    command not in {"plan", "auth-snippet"},
                )

    def test_mount_protocol_is_versioned_and_contains_no_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout with spaces"
            state = root / "state"
            lock = root / "lock"
            for path in (checkout, state, lock):
                path.mkdir()
            plan = MountPlan(
                command="doctor",
                socket=True,
                environment_sha256="a" * 64,
                mounts=(
                    MountSpec.capture(checkout, "ro"),
                    MountSpec.capture(state, "ro"),
                ),
                lock_source=lock,
            )

            rendered = plan.render()

        self.assertEqual(
            rendered.splitlines()[0],
            f"BTCTL_MOUNT_PLAN\t2\tdoctor\tunraid\t{'a' * 64}",
        )
        self.assertRegex(rendered, rf"mount\tro\t{checkout}\t[0-9:]+[0-9a-f:]+")
        self.assertRegex(
            rendered,
            rf"lock\tro\t{lock}\t/run/btctl-lock\t[0-9:]+[0-9a-f:]+",
        )
        self.assertIn("socket\tyes", rendered)
        self.assertNotIn("LLM_API_KEY", rendered)

    def test_mount_source_identity_changes_when_a_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            first = MountSpec.capture(source, "rw")
            source.rename(Path(directory) / "old-source")
            source.mkdir()
            second = MountSpec.capture(source, "rw")

            self.assertNotEqual(first.identity, second.identity)

    def test_mount_plan_is_bound_to_the_exact_environment_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = root / "install.env"
            environment.write_text("BT_INSTALL_PROFILE=unraid\n", encoding="utf-8")
            environment.chmod(0o600)
            managed = root / "share" / "cwa-translate"
            state = managed / "state"
            data = managed / "data"
            backup = root / "share" / "backups"
            template = root / "boot" / "templates-user"
            lock = root / "lock"
            for path in (state, data, backup, template, lock):
                path.mkdir(parents=True)
            config = SimpleNamespace(
                install_profile="unraid",
                state_dir=str(state),
                data_dir=str(data),
                backup_dir=str(backup),
                unraid_template_dir=str(template),
            )

            with (
                mock.patch("btctl_container.HOST_LOCK_SOURCE", lock),
                mock.patch("btctl_container._validate_repository", return_value=checkout),
                mock.patch(
                    "btctl_container._config_for_command", return_value=(config, {})
                ),
                mock.patch(
                    "btctl_container.validate_storage_path",
                    side_effect=lambda path, _label: path,
                ),
                mock.patch(
                    "btctl_container._validate_template_path",
                    side_effect=lambda path: path,
                ),
                mock.patch(
                    "btctl_container._storage_minimum", return_value=root / "share"
                ),
            ):
                first = create_mount_plan("doctor", checkout, environment, "a" * 40)
                environment.write_text(
                    "BT_INSTALL_PROFILE=unraid\nBT_INSTALL_NAME=changed\n",
                    encoding="utf-8",
                )
                second = create_mount_plan("doctor", checkout, environment, "a" * 40)

            self.assertEqual(
                first.environment_sha256,
                hashlib.sha256(b"BT_INSTALL_PROFILE=unraid\n").hexdigest(),
            )
            self.assertNotEqual(first.environment_sha256, second.environment_sha256)
            self.assertNotEqual(first.render(), second.render())


class ContainerPathPolicyTests(unittest.TestCase):
    @staticmethod
    def _effective_mode(plan: MountPlan, target: Path) -> str | None:
        candidates = [
            mount
            for mount in plan.mounts
            if mount.path == target or mount.path in target.parents
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda mount: len(mount.path.parts)).mode

    def test_existing_state_and_upgrade_data_mount_the_required_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            share = Path(directory) / "share"
            managed = share / "appdata" / "cwa-translate"
            state = managed / "state"
            data = managed / "data"
            state.mkdir(parents=True)
            data.mkdir()

            self.assertEqual(
                mount_source_for_path("doctor", "state", state, share),
                managed,
            )
            self.assertEqual(
                mount_source_for_path("upgrade", "data", data, share),
                managed,
            )
            self.assertEqual(
                mount_source_for_path("doctor", "data", data, share),
                data,
            )

    def test_rollback_uses_the_journaled_legacy_path_not_the_environment(self):
        config = SimpleNamespace(state_dir="/mnt/user/appdata/cwa-translate/state")
        with mock.patch(
            "btctl_container.MigrationJournalStore.load",
            return_value={"legacy_data_dir": "/mnt/user/appdata/journaled-v214"},
        ):
            selected = legacy_data_path(
                "rollback",
                config,
                {"BT_LEGACY_DATA_DIR": "/mnt/user/appdata/stale-env-v214"},
            )

        self.assertEqual(selected, "/mnt/user/appdata/journaled-v214")
        self.assertEqual(
            legacy_data_path(
                "upgrade",
                config,
                {"BT_LEGACY_DATA_DIR": "/mnt/user/appdata/env-v214"},
            ),
            "/mnt/user/appdata/env-v214",
        )

    def test_storage_paths_require_an_existing_pool_or_share_root(self):
        with tempfile.TemporaryDirectory() as directory:
            storage_root = Path(directory) / "mnt"
            user_root = storage_root / "user"
            share = user_root / "appdata"
            share.mkdir(parents=True)

            accepted = validate_storage_path(
                share / "cwa-translate" / "state",
                "BT_STATE_DIR",
                storage_root=storage_root,
            )

            self.assertEqual(
                accepted, share / "cwa-translate" / "state"
            )
            with self.assertRaisesRegex(ConfigError, "below an existing"):
                validate_storage_path(
                    share,
                    "BT_STATE_DIR",
                    storage_root=storage_root,
                )
            with self.assertRaisesRegex(ConfigError, "existing Unraid share"):
                validate_storage_path(
                    user_root / "apdata" / "cwa-translate" / "state",
                    "BT_STATE_DIR",
                    storage_root=storage_root,
                )
            with self.assertRaisesRegex(ConfigError, "under"):
                validate_storage_path(
                    Path(directory) / "srv" / "state",
                    "BT_STATE_DIR",
                    storage_root=storage_root,
                )

    def test_storage_paths_reject_symlinked_components_and_mount_delimiters(self):
        with tempfile.TemporaryDirectory() as directory:
            storage_root = Path(directory) / "mnt"
            pool = storage_root / "pool"
            real = storage_root / "real"
            pool.mkdir(parents=True)
            real.mkdir()
            (pool / "linked").symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(ConfigError, "symbolic link"):
                validate_storage_path(
                    pool / "linked" / "state",
                    "BT_STATE_DIR",
                    storage_root=storage_root,
                )
            for name in ("bad,name", "bad\tname", "bad\nname"):
                with self.subTest(name=name), self.assertRaisesRegex(
                    ConfigError, "Docker mount"
                ):
                    validate_storage_path(
                        pool / name,
                        "BT_STATE_DIR",
                        storage_root=storage_root,
                    )

    def test_uninstall_mounts_only_state_data_template_and_dedicated_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = root / "install.env"
            environment.write_text("BT_INSTALL_PROFILE=unraid\n", encoding="utf-8")
            environment.chmod(0o600)
            managed = root / "share" / "cwa-translate"
            state = managed / "state"
            data = managed / "data"
            backup = root / "share" / "backups"
            template = root / "boot" / "templates-user"
            lock = root / "lock"
            for path in (state, data, backup, template, lock):
                path.mkdir(parents=True)
            config = SimpleNamespace(
                install_profile="unraid",
                state_dir=str(state),
                data_dir=str(data),
                backup_dir=str(backup),
                unraid_template_dir=str(template),
            )

            with (
                mock.patch("btctl_container.HOST_LOCK_SOURCE", lock),
                mock.patch("btctl_container._validate_repository", return_value=checkout),
                mock.patch(
                    "btctl_container._config_for_command",
                    return_value=(config, {}),
                ),
                mock.patch(
                    "btctl_container.validate_storage_path",
                    side_effect=lambda path, _label: path,
                ),
                mock.patch(
                    "btctl_container._validate_template_path",
                    side_effect=lambda path: path,
                ),
                mock.patch(
                    "btctl_container._storage_minimum",
                    return_value=root / "share",
                ),
            ):
                plan = create_mount_plan(
                    "uninstall", checkout, environment, "a" * 40
                )

            self.assertEqual(plan.lock_source, lock)
            self.assertEqual(self._effective_mode(plan, state), "rw")
            self.assertEqual(self._effective_mode(plan, data), "ro")

    def test_missing_state_parent_write_is_narrowed_by_read_only_data_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = root / "install.env"
            environment.write_text("BT_INSTALL_PROFILE=unraid\n", encoding="utf-8")
            environment.chmod(0o600)
            managed = root / "share" / "cwa-translate"
            managed.mkdir(parents=True)
            state = managed / "state"
            data = managed / "data"
            data.mkdir()
            backup = root / "share" / "backups"
            backup.mkdir()
            template = root / "boot" / "templates-user"
            template.mkdir(parents=True)
            lock = root / "lock"
            lock.mkdir()
            config = SimpleNamespace(
                install_profile="unraid",
                state_dir=str(state),
                data_dir=str(data),
                backup_dir=str(backup),
                unraid_template_dir=str(template),
            )

            with (
                mock.patch("btctl_container.HOST_LOCK_SOURCE", lock),
                mock.patch("btctl_container._validate_repository", return_value=checkout),
                mock.patch(
                    "btctl_container._config_for_command",
                    return_value=(config, {}),
                ),
                mock.patch(
                    "btctl_container.validate_storage_path",
                    side_effect=lambda path, _label: path,
                ),
                mock.patch(
                    "btctl_container._validate_template_path",
                    side_effect=lambda path: path,
                ),
                mock.patch(
                    "btctl_container._storage_minimum",
                    return_value=root / "share",
                ),
            ):
                plan = create_mount_plan("adopt", checkout, environment, "a" * 40)

            self.assertEqual(plan.lock_source, lock)
            self.assertEqual(self._effective_mode(plan, state), "rw")
            self.assertEqual(self._effective_mode(plan, data), "ro")


if __name__ == "__main__":
    unittest.main(verbosity=2)
