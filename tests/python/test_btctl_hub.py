"""Schema-3 lifecycle contracts for the universal reader hub."""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from btctl_compose import InstallError
from btctl_core import (
    ConfigError,
    DeploymentPlan,
    DeploymentState,
    InstallConfig,
    ReleaseIdentity,
    StateStore,
)
from btctl_hub import (
    HUB_STATE_SCHEMA_VERSION,
    HubDoctor,
    HubInstallConfig,
    HubInstaller,
    HubPlan,
    HubState,
    HubStateStore,
    HubTopologyMigration,
    HubUninstaller,
    render_hub_compose,
)
from tests.python.test_btctl_compose import values as split_values


IDENTITY = ReleaseIdentity(
    version="2.2.0",
    sha="0123456789abcdef0123456789abcdef01234567",
)


def hub_values(root: Path) -> dict[str, str]:
    return {
        "BT_TOPOLOGY": "hub",
        "BT_INSTALL_PROFILE": "compose-existing",
        "BT_INSTALL_NAME": "book-translator-hub",
        "BT_STATE_DIR": str(root / "state"),
        "BT_DATA_DIR": str(root / "data"),
        "BT_BACKUP_DIR": str(root / "backup"),
        "BT_ENABLE_CWA": "true",
        "BT_CWA_PUBLIC_ORIGIN": "https://books.example.test",
        "BT_CWA_READER_UPSTREAM": "http://calibre-web:8083",
        "BT_CWA_READER_CONTAINER": "calibre-web",
        "BT_CWA_READER_NETWORK": "cwa_default",
        "BT_CWA_READER_VERSION": "4.0.6",
        "BT_CWA_AUTH_PROFILE": "reader-session",
        "BT_CWA_READER_CONNECTOR_ID": "01234567-89ab-4cde-8123-0123456789ab",
        "BT_CWA_PUBLISHED_PORT": "8385",
        "BT_ENABLE_KAVITA": "true",
        "BT_KAVITA_PUBLIC_ORIGIN": "https://kavita.example.test",
        "BT_KAVITA_READER_UPSTREAM": "http://kavita:5000",
        "BT_KAVITA_READER_CONTAINER": "kavita",
        "BT_KAVITA_READER_NETWORK": "kavita_default",
        "BT_KAVITA_READER_VERSION": "0.9.0.2",
        "BT_KAVITA_AUTH_PROFILE": "reader-session",
        "BT_KAVITA_READER_CONNECTOR_ID": "11234567-89ab-4cde-8123-0123456789ab",
        "BT_KAVITA_PUBLISHED_PORT": "8386",
        "LLM_PROVIDER": "gemini",
        "LLM_MODEL": "gemini-2.5-flash",
        "LLM_API_KEY": "fake-test-secret",
        "BT_MAX_CONCURRENT": "2",
        "BT_MAX_UPSTREAM_INFLIGHT": "2",
    }


class TopologyDocker:
    """Small exact-runtime fake for split-to-hub transaction tests."""

    def __init__(self):
        self.containers: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[object, ...]] = []

    def inspect_container(self, name):
        return self.containers.get(name)

    def stop_container(self, name):
        self.calls.append(("stop", name))
        self.containers[name]["State"]["Status"] = "exited"

    def start_container(self, name):
        self.calls.append(("start", name))
        self.containers[name]["State"]["Status"] = "running"

    def wait_healthy(self, names, timeout):
        self.calls.append(("healthy", tuple(names), timeout))


def split_source(
    root: Path,
    reader: str,
    docker: TopologyDocker,
    *,
    api_status: str = "running",
) -> Path:
    source_root = root / f"split-{reader}"
    config = InstallConfig.from_mapping(
        split_values(source_root, reader=reader), IDENTITY
    )
    plan = DeploymentPlan.from_config(config)
    resources = copy.deepcopy(plan.resources)
    for role in ("api", "proxy"):
        name = str(resources[role]["name"])
        identifier = f"{reader}-{role}-id"
        resources[role]["id"] = identifier
        docker.containers[name] = {
            "Id": identifier,
            "State": {"Status": api_status if role == "api" else "running"},
        }
    state = replace(
        DeploymentState.new(
            install_id=(
                "31234567-89ab-4cde-8123-0123456789ab"
                if reader == "cwa"
                else "41234567-89ab-4cde-8123-0123456789ab"
            ),
            plan=plan,
        ),
        resources=resources,
    )
    StateStore(Path(config.state_dir)).save(state)
    data_dir = Path(config.data_dir)
    data_dir.mkdir(mode=0o700, parents=True)
    with closing(sqlite3.connect(data_dir / "translations.db")) as database:
        database.execute("CREATE TABLE translations (key TEXT PRIMARY KEY, value TEXT)")
        database.execute("INSERT INTO translations VALUES (?, ?)", (reader, reader))
        database.commit()
    (data_dir / f"{reader}.marker").write_text(reader, encoding="utf-8")
    key = data_dir / "reader_session_key"
    key.write_bytes((reader.encode("ascii") * 32)[:32])
    key.chmod(0o600)
    return Path(config.state_dir)


class HubBtctlTests(unittest.TestCase):
    def setUp(self):
        self.root_patch = mock.patch("btctl_hub._effective_uid", return_value=0)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()

    def test_plan_is_schema_three_and_never_contains_provider_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubInstallConfig.from_mapping(hub_values(Path(directory)), IDENTITY)
            payload = HubPlan.from_config(config).to_dict()

        self.assertEqual(payload["schema_version"], HUB_STATE_SCHEMA_VERSION)
        self.assertEqual(payload["topology"], "hub")
        self.assertEqual(set(payload["readers"]), {"cwa", "kavita"})
        self.assertNotIn("fake-test-secret", json.dumps(payload))
        self.assertEqual(payload["resources"]["hub"]["published_ports"], [8385, 8386])

    def test_reader_upstream_must_match_declared_external_container(self):
        with tempfile.TemporaryDirectory() as directory:
            values = hub_values(Path(directory))
            values["BT_KAVITA_READER_CONTAINER"] = "not-kavita"
            with self.assertRaisesRegex(ConfigError, "UPSTREAM.*container"):
                HubInstallConfig.from_mapping(values, IDENTITY)

    def test_compose_has_one_container_and_no_secret_in_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubInstallConfig.from_mapping(hub_values(Path(directory)), IDENTITY)
            plan = HubPlan.from_config(config)
            document = render_hub_compose(
                config,
                plan,
                "21234567-89ab-4cde-8123-0123456789ab",
            )

        self.assertEqual(set(document["services"]), {"hub"})
        service = document["services"]["hub"]
        self.assertEqual(service["environment"]["BT_ROLE"], "hub")
        self.assertEqual(
            service["ports"],
            [
                {"target": 8080, "published": 8385, "protocol": "tcp"},
                {"target": 8081, "published": 8386, "protocol": "tcp"},
            ],
        )
        self.assertEqual(set(service["networks"]), {"reader_cwa", "reader_kavita"})
        self.assertNotIn("fake-test-secret", json.dumps(service["labels"]))

    def test_schema_three_state_is_private_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root), IDENTITY)
            plan = HubPlan.from_config(config)
            state = HubState.new(
                install_id="21234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )
            store = HubStateStore(Path(config.state_dir))
            store.save(state)
            loaded = store.load()

            self.assertEqual(loaded, state)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(config.state_dir).stat().st_mode & 0o777, 0o700)

    def test_hub_profile_rejects_the_split_only_authentik_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            values = hub_values(Path(directory))
            values["BT_ENABLE_KAVITA"] = "false"
            values["BT_CWA_AUTH_PROFILE"] = "authentik-forwarded"
            with self.assertRaisesRegex(ConfigError, "unsupported"):
                HubInstallConfig.from_mapping(values, IDENTITY)

    def test_compose_install_builds_one_hub_and_commits_verified_state(self):
        class Docker:
            def __init__(self):
                self.image = None
                self.hub = None
                self.document = None
                self.calls = []

            def require_available(self):
                self.calls.append("available")

            def inspect_container(self, name):
                if name == "calibre-web":
                    return {
                        "State": {"Status": "running"},
                        "Config": {"Image": "reader:4.0.6"},
                        "NetworkSettings": {"Networks": {"cwa_default": {}}},
                    }
                if name == "kavita":
                    return {
                        "State": {"Status": "running"},
                        "Config": {"Image": "reader:0.9.0.2"},
                        "NetworkSettings": {"Networks": {"kavita_default": {}}},
                    }
                return self.hub

            def build_image(self, _repository, image, labels):
                self.calls.append("build")
                self.image = {
                    "Id": "sha256:" + "a" * 64,
                    "Config": {"Labels": labels},
                }

            def inspect_image(self, _name):
                return self.image

            def prepare_hub_data_directory(self, _image, path, readers):
                self.calls.append("data")
                path.chmod(0o700)
                self.calls.append(("data-readers", tuple(readers)))

            def compose_validate(self, document, _project):
                self.calls.append("validate")
                self.document = json.loads(document.read_text(encoding="utf-8"))

            def compose_up(self, _document, _project):
                self.calls.append("up")
                service = self.document["services"]["hub"]
                self.hub = {
                    "Id": "hub-container-id",
                    "Image": self.image["Id"],
                    "State": {"Status": "running", "Health": {"Status": "healthy"}},
                    "Config": {
                        "Labels": service["labels"],
                        "User": "101:102",
                        "Env": [
                            f"{key}={value}"
                            for key, value in service["environment"].items()
                        ],
                    },
                    "HostConfig": {
                        "ReadonlyRootfs": True,
                        "Privileged": False,
                        "CapDrop": ["ALL"],
                        "SecurityOpt": ["no-new-privileges:true"],
                        "PidsLimit": 384,
                        "PortBindings": {
                            "8080/tcp": [{"HostPort": "8385"}],
                            "8081/tcp": [{"HostPort": "8386"}],
                        },
                    },
                    "Mounts": [{
                        "Type": "bind",
                        "Source": service["volumes"][0]["source"],
                        "Destination": "/app/data",
                        "RW": True,
                    }],
                    "NetworkSettings": {
                        "Networks": {"cwa_default": {}, "kavita_default": {}}
                    },
                }

            def wait_healthy(self, names, _timeout):
                self.calls.append(("healthy", tuple(names)))

            def probe_http(self, _container, _url):
                self.calls.append("http")

            def probe_reader_auth(self, _container, reader, _url):
                self.calls.append(("reader-auth", reader))

            def probe_sqlite(self, _container, _path):
                self.calls.append("sqlite")

            def compose_down(self, _document, _project):
                self.calls.append("down")
                self.hub = None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root), IDENTITY)
            docker = Docker()
            state = HubInstaller(docker).install(
                config,
                HubPlan.from_config(config),
                root,
            )

            self.assertEqual(state.schema_version, 3)
            self.assertEqual(state.status, "installed")
            self.assertEqual(state.resources["hub"]["id"], "hub-container-id")
            self.assertEqual(HubStateStore(Path(config.state_dir)).load(), state)
            self.assertEqual(
                docker.calls[:5],
                [
                    "available",
                    "build",
                    "data",
                    ("data-readers", ("cwa", "kavita")),
                    "validate",
                ],
            )
            self.assertEqual(docker.calls.count("http"), 4)
            self.assertEqual(
                [call for call in docker.calls if isinstance(call, tuple) and call[0] == "reader-auth"],
                [("reader-auth", "cwa"), ("reader-auth", "kavita")],
            )
            self.assertEqual(docker.calls.count("sqlite"), 2)

    def test_hub_dependency_probe_fails_when_one_reader_auth_boundary_is_down(self):
        class Docker:
            def __init__(self):
                self.calls = []

            def probe_http(self, _container, url):
                self.calls.append(("http", url))

            def probe_reader_auth(self, _container, reader, url):
                self.calls.append(("auth", reader, url))
                if reader == "kavita":
                    raise InstallError("kavita auth unavailable")

            def probe_sqlite(self, _container, path):
                self.calls.append(("sqlite", path))

        with tempfile.TemporaryDirectory() as directory:
            config = HubInstallConfig.from_mapping(
                hub_values(Path(directory)), IDENTITY
            )
            docker = Docker()

            with self.assertRaisesRegex(InstallError, "kavita auth unavailable"):
                HubInstaller(docker)._probe(config)

            self.assertIn(
                ("http", "http://calibre-web:8083/"), docker.calls
            )
            self.assertIn(
                ("auth", "kavita", "http://kavita:5000/api/Account"),
                docker.calls,
            )

    def test_hub_doctor_deep_probes_only_after_structural_success(self):
        class Docker:
            def __init__(self):
                self.calls = []

            def require_available(self):
                self.calls.append(("available",))

            def probe_hub_providers(self, container):
                self.calls.append(("probe_hub_providers", container))

        with tempfile.TemporaryDirectory() as directory:
            config = HubInstallConfig.from_mapping(
                hub_values(Path(directory)), IDENTITY
            )
            plan = HubPlan.from_config(config)
            state = HubState.new(
                install_id="21234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )
            docker = Docker()
            doctor = HubDoctor(docker)
            with (
                mock.patch.object(HubStateStore, "load", return_value=state),
                mock.patch.object(HubInstaller, "_verify_readers"),
                mock.patch.object(HubInstaller, "_verify_hub"),
                mock.patch.object(HubInstaller, "_probe"),
                mock.patch.object(HubDoctor, "_verify_data"),
            ):
                ordinary = doctor.run(config, plan)
                deep = doctor.run(config, plan, deep=True)

            self.assertTrue(ordinary.ok, ordinary.to_dict())
            self.assertTrue(deep.ok, deep.to_dict())
            self.assertEqual(
                [call for call in docker.calls if call[0] == "probe_hub_providers"],
                [("probe_hub_providers", config.install_name)],
            )

            with (
                mock.patch.object(HubStateStore, "load", return_value=state),
                mock.patch.object(HubInstaller, "_verify_readers"),
                mock.patch.object(HubInstaller, "_verify_hub"),
                mock.patch.object(HubInstaller, "_probe"),
                mock.patch.object(
                    HubDoctor,
                    "_verify_data",
                    side_effect=InstallError("structural drift"),
                ),
            ):
                failed = doctor.run(config, plan, deep=True)

            self.assertFalse(failed.ok)
            self.assertEqual(
                [call for call in docker.calls if call[0] == "probe_hub_providers"],
                [("probe_hub_providers", config.install_name)],
            )

    def test_topology_migration_rejects_overlapping_source_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            migration = HubTopologyMigration(docker)
            overlapping_data = Path(config.data_dir)
            overlapping_data.mkdir(parents=True)
            with closing(sqlite3.connect(overlapping_data / "translations.db")):
                pass
            sources = {
                "cwa": {
                    "reader": "cwa",
                    "state_dir": str(root / "split-cwa-state"),
                    "install_id": "31234567-89ab-4cde-8123-0123456789ab",
                    "api_name": "cwa-api",
                    "api_id": "cwa-api-id",
                    "proxy_name": "cwa-proxy",
                    "proxy_id": "cwa-proxy-id",
                    "data_dir": config.data_dir,
                },
                "kavita": {
                    "reader": "kavita",
                    "state_dir": str(root / "split-kavita-state"),
                    "install_id": "41234567-89ab-4cde-8123-0123456789ab",
                    "api_name": "kavita-api",
                    "api_id": "kavita-api-id",
                    "proxy_name": "kavita-proxy",
                    "proxy_id": "kavita-proxy-id",
                    "data_dir": str(root / "split-kavita-data"),
                },
            }
            statuses = {
                reader: {"api": "running", "proxy": "running"}
                for reader in sources
            }

            with mock.patch.object(migration, "_sources", return_value=sources), \
                    mock.patch.object(
                        migration, "_verify_source_containers", return_value=statuses
                    ), self.assertRaisesRegex(InstallError, "overlap"):
                migration.migrate(
                    config,
                    plan,
                    root,
                    [root / "split-cwa-state", root / "split-kavita-state"],
                )

            self.assertFalse([call for call in docker.calls if call[0] == "stop"])
            self.assertFalse(migration._journal_path(config).exists())

    def test_topology_migration_rejects_unrelated_target_data_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            target = Path(config.data_dir)
            target.mkdir(parents=True)
            (target / "unrelated.txt").write_text("do not mutate", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "empty|unexpected"):
                HubTopologyMigration(docker).migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            self.assertFalse([call for call in docker.calls if call[0] == "stop"])
            self.assertEqual((target / "unrelated.txt").read_text(), "do not mutate")

    def test_topology_migration_requires_root_before_private_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)

            with mock.patch(
                "btctl_hub._effective_uid", return_value=1000
            ), self.assertRaisesRegex(InstallError, "must run as root"):
                HubTopologyMigration(docker).migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            self.assertFalse([call for call in docker.calls if call[0] == "stop"])
            self.assertFalse(Path(config.state_dir).exists())
            self.assertFalse(Path(config.data_dir).exists())

    def test_topology_migration_recovers_published_and_complete_partial_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            migration = HubTopologyMigration(docker)
            sources = migration._sources(config, [cwa_state, kavita_state])
            statuses = migration._verify_source_containers(sources)
            Path(config.state_dir).mkdir(parents=True)
            migration._save_journal(
                config,
                {
                    "status": "copying",
                    "migration_id": "61234567-89ab-4cde-8123-0123456789ab",
                    "hub_fingerprint": plan.config_fingerprint,
                    "sources": sources,
                    "initial_statuses": statuses,
                    "copied": {},
                },
            )
            target_root = Path(config.data_dir)
            target_root.mkdir(parents=True)
            shutil.copytree(Path(str(sources["cwa"]["data_dir"])), target_root / "cwa")
            shutil.copytree(
                Path(str(sources["kavita"]["data_dir"])),
                target_root / ".kavita.migration.partial",
            )
            for source in sources.values():
                for role in ("api", "proxy"):
                    docker.containers[str(source[f"{role}_name"])]["State"][
                        "Status"
                    ] = "exited"
            installed = HubState.new(
                install_id="71234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )

            def commit_hub(*_args, **_kwargs):
                HubStateStore(Path(config.state_dir)).save(installed)
                return installed

            with mock.patch(
                "btctl_hub.HubInstaller.install", side_effect=commit_hub
            ):
                result = migration.migrate(
                    config, plan, root, [kavita_state, cwa_state]
                )

            self.assertEqual(result, installed)
            journal = migration._load_journal(config)
            self.assertEqual(set(journal["copied"]), {"cwa", "kavita"})
            self.assertFalse((target_root / ".kavita.migration.partial").exists())
            self.assertTrue((target_root / "kavita" / "kavita.marker").is_file())
            self.assertEqual(
                (target_root / "cwa" / "reader_session_key").stat().st_mode
                & 0o777,
                0o600,
            )

    def test_topology_migration_failure_restores_only_initially_running_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker, api_status="exited")
            migration = HubTopologyMigration(docker)

            with mock.patch(
                "btctl_hub.HubInstaller.install",
                side_effect=InstallError("synthetic hub failure"),
            ), self.assertRaisesRegex(InstallError, "synthetic hub failure"):
                migration.migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            journal = migration._load_journal(config)
            self.assertEqual(journal["status"], "failed")
            self.assertEqual(
                docker.containers["cwa-translate-test-api"]["State"]["Status"],
                "running",
            )
            self.assertEqual(
                docker.containers["kavita-translate-test-api"]["State"]["Status"],
                "exited",
            )
            self.assertIn(("start", "cwa-translate-test-api"), docker.calls)
            self.assertNotIn(("start", "kavita-translate-test-api"), docker.calls)

    def test_post_stop_journal_failure_restarts_all_original_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            migration = HubTopologyMigration(docker)
            original_save = migration._save_journal
            save_count = 0

            def fail_first_copy_evidence(save_config, journal):
                nonlocal save_count
                save_count += 1
                if save_count == 3:
                    raise InstallError("synthetic journal write failure")
                return original_save(save_config, journal)

            with mock.patch.object(
                migration, "_save_journal", side_effect=fail_first_copy_evidence
            ), self.assertRaisesRegex(InstallError, "journal write failure"):
                migration.migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            self.assertEqual(migration._load_journal(config)["status"], "failed")
            self.assertEqual(
                {
                    name
                    for name, container in docker.containers.items()
                    if name.endswith(("-api", "-proxy"))
                    and container["State"]["Status"] == "running"
                },
                {
                    "cwa-translate-test-api",
                    "cwa-translate-test-proxy",
                    "kavita-translate-test-api",
                    "kavita-translate-test-proxy",
                },
            )

    def test_final_journal_failure_leaves_verified_hub_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            migration = HubTopologyMigration(docker)
            installed = HubState.new(
                install_id="71234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )
            original_save = migration._save_journal

            def commit_hub(*_args, **_kwargs):
                HubStateStore(Path(config.state_dir)).save(installed)
                return installed

            def fail_committed_save(save_config, journal):
                if journal.get("status") == "committed":
                    raise InstallError("synthetic final journal failure")
                return original_save(save_config, journal)

            with mock.patch(
                "btctl_hub.HubInstaller.install", side_effect=commit_hub
            ), mock.patch.object(
                migration, "_save_journal", side_effect=fail_committed_save
            ), self.assertRaisesRegex(InstallError, "journal commit is pending"):
                migration.migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            self.assertEqual(migration._load_journal(config)["status"], "copying")
            self.assertEqual(
                HubStateStore(Path(config.state_dir)).load().status, "installed"
            )
            self.assertTrue(
                (Path(config.data_dir) / "cwa" / "reader_session_key").is_file()
            )
            self.assertTrue(
                (Path(config.data_dir) / "kavita" / "reader_session_key").is_file()
            )
            self.assertFalse([call for call in docker.calls if call[0] == "start"])

            with mock.patch("btctl_hub.HubInstaller._verify_hub"):
                resumed = migration.migrate(
                    config, plan, root, [kavita_state, cwa_state]
                )

            self.assertEqual(resumed, installed)
            self.assertEqual(migration._load_journal(config)["status"], "committed")
            self.assertFalse([call for call in docker.calls if call[0] == "start"])

    def test_topology_migration_fails_closed_when_partial_hub_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            migration = HubTopologyMigration(docker)

            def leave_partial_hub(*_args, **_kwargs):
                docker.containers[config.install_name] = {
                    "Id": "partial-hub-id",
                    "State": {"Status": "running"},
                }
                raise InstallError("synthetic partial hub failure")

            with mock.patch(
                "btctl_hub.HubInstaller.install", side_effect=leave_partial_hub
            ), self.assertRaisesRegex(InstallError, "cleanup is incomplete"):
                migration.migrate(
                    config, plan, root, [cwa_state, kavita_state]
                )

            journal = migration._load_journal(config)
            self.assertEqual(journal["status"], "failed")
            self.assertEqual(journal["hub_cleanup"], "failed")
            self.assertFalse([call for call in docker.calls if call[0] == "start"])

    def test_committed_topology_migration_copies_both_readers_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root / "hub"), IDENTITY)
            plan = HubPlan.from_config(config)
            docker = TopologyDocker()
            cwa_state = split_source(root, "cwa", docker)
            kavita_state = split_source(root, "kavita", docker)
            migration = HubTopologyMigration(docker)
            installed = HubState.new(
                install_id="51234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )

            def commit_hub(*_args, **_kwargs):
                HubStateStore(Path(config.state_dir)).save(installed)
                return installed

            with mock.patch(
                "btctl_hub.HubInstaller.install", side_effect=commit_hub
            ):
                result = migration.migrate(
                    config, plan, root, [kavita_state, cwa_state]
                )

            self.assertEqual(result, installed)
            self.assertEqual(
                (Path(config.data_dir) / "cwa" / "cwa.marker").read_text(), "cwa"
            )
            self.assertEqual(
                (Path(config.data_dir) / "kavita" / "kavita.marker").read_text(),
                "kavita",
            )
            self.assertEqual(
                (Path(config.data_dir) / "kavita" / "reader_session_key").stat().st_mode
                & 0o777,
                0o600,
            )
            self.assertEqual(migration._load_journal(config)["status"], "committed")
            self.assertFalse([call for call in docker.calls if call[0] == "start"])

            uninstalled = replace(installed, status="uninstalled")
            with mock.patch(
                "btctl_hub.HubUninstaller.uninstall", return_value=uninstalled
            ):
                rolled_back = migration.rollback(config, plan)

            self.assertEqual(rolled_back.status, "rolled_back")
            self.assertEqual(migration._load_journal(config)["status"], "rolled_back")
            self.assertEqual(
                {
                    call[1]
                    for call in docker.calls
                    if call[0] == "start"
                },
                {
                    "cwa-translate-test-api",
                    "cwa-translate-test-proxy",
                    "kavita-translate-test-api",
                    "kavita-translate-test-proxy",
                },
            )

    def test_hub_uninstall_removes_only_managed_reader_keys_and_is_retry_safe(self):
        class Docker:
            def __init__(self):
                self.calls = []

            def inspect_container(self, _name):
                return None

            def remove_hub_data_credentials(self, _image, path, readers):
                self.calls.append(tuple(readers))
                for reader in readers:
                    key = Path(path) / reader / "reader_session_key"
                    if key.exists():
                        self.assert_private_key(key)
                        key.unlink()

            @staticmethod
            def assert_private_key(path):
                if path.stat().st_mode & 0o777 != 0o600 or path.stat().st_size != 32:
                    raise AssertionError("test fixture key was not private")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = HubInstallConfig.from_mapping(hub_values(root), IDENTITY)
            plan = HubPlan.from_config(config)
            state = HubState.new(
                install_id="81234567-89ab-4cde-8123-0123456789ab",
                plan=plan,
            )
            HubStateStore(Path(config.state_dir)).save(state)
            for reader in ("cwa", "kavita"):
                data = Path(config.data_dir) / reader
                data.mkdir(parents=True)
                (data / "keep.marker").write_text(reader, encoding="utf-8")
                key = data / "reader_session_key"
                key.write_bytes(b"k" * 32)
                key.chmod(0o600)
            docker = Docker()

            removed = HubUninstaller(docker).uninstall(config, plan)
            repeated = HubUninstaller(docker).uninstall(config, plan)

            self.assertEqual(removed.status, "uninstalled")
            self.assertEqual(repeated, removed)
            self.assertEqual(docker.calls, [("cwa", "kavita")])
            for reader in ("cwa", "kavita"):
                self.assertFalse(
                    (Path(config.data_dir) / reader / "reader_session_key").exists()
                )
                self.assertTrue(
                    (Path(config.data_dir) / reader / "keep.marker").is_file()
                )
                self.assertTrue(removed.resources[f"session_key_{reader}"]["removed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
