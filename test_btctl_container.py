from __future__ import annotations

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
            "uninstall": (("state", "rw"), ("template", "rw")),
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
        plan = MountPlan(
            command="doctor",
            socket=True,
            mounts=(
                MountSpec(Path("/checkout with spaces"), "ro"),
                MountSpec(Path("/mnt/user/appdata/cwa-translate/state"), "ro"),
            ),
            lock_source=Path("/mnt/user/appdata/cwa-translate"),
        )

        rendered = plan.render()

        self.assertEqual(
            rendered.splitlines()[0], "BTCTL_MOUNT_PLAN\t1\tdoctor\tunraid"
        )
        self.assertIn("mount\tro\t/checkout with spaces", rendered)
        self.assertIn(
            "lock\tro\t/mnt/user/appdata/cwa-translate\t/run/btctl-lock",
            rendered,
        )
        self.assertIn("socket\tyes", rendered)
        self.assertNotIn("LLM_API_KEY", rendered)


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

    def test_existing_state_uses_a_dedicated_lock_mount_without_exposing_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = root / "install.env"
            environment.write_text("BT_INSTALL_PROFILE=unraid\n", encoding="utf-8")
            managed = root / "share" / "cwa-translate"
            state = managed / "state"
            data = managed / "data"
            backup = root / "share" / "backups"
            template = root / "boot" / "templates-user"
            for path in (state, data, backup, template):
                path.mkdir(parents=True)
            config = SimpleNamespace(
                install_profile="unraid",
                state_dir=str(state),
                data_dir=str(data),
                backup_dir=str(backup),
                unraid_template_dir=str(template),
            )

            with (
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

            self.assertEqual(
                plan.lock_source, Path("/run/cwa-translate-btctl-locks")
            )
            self.assertEqual(self._effective_mode(plan, state), "rw")
            self.assertIsNone(self._effective_mode(plan, data))

    def test_missing_state_parent_write_is_narrowed_by_read_only_data_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = root / "install.env"
            environment.write_text("BT_INSTALL_PROFILE=unraid\n", encoding="utf-8")
            managed = root / "share" / "cwa-translate"
            managed.mkdir(parents=True)
            state = managed / "state"
            data = managed / "data"
            data.mkdir()
            backup = root / "share" / "backups"
            backup.mkdir()
            template = root / "boot" / "templates-user"
            template.mkdir(parents=True)
            config = SimpleNamespace(
                install_profile="unraid",
                state_dir=str(state),
                data_dir=str(data),
                backup_dir=str(backup),
                unraid_template_dir=str(template),
            )

            with (
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

            self.assertEqual(
                plan.lock_source, Path("/run/cwa-translate-btctl-locks")
            )
            self.assertEqual(self._effective_mode(plan, state), "rw")
            self.assertEqual(self._effective_mode(plan, data), "ro")


if __name__ == "__main__":
    unittest.main(verbosity=2)
