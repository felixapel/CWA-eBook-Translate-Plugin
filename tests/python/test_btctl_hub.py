"""Schema-3 lifecycle contracts for the universal reader hub."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from btctl_core import ConfigError, ReleaseIdentity
from btctl_hub import (
    HUB_STATE_SCHEMA_VERSION,
    HubInstallConfig,
    HubInstaller,
    HubPlan,
    HubState,
    HubStateStore,
    render_hub_compose,
)


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


class HubBtctlTests(unittest.TestCase):
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

            def prepare_data_directory(self, _image, path):
                self.calls.append("data")
                path.chmod(0o700)

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
            self.assertEqual(docker.calls[:4], ["available", "build", "data", "validate"])
            self.assertEqual(docker.calls.count("http"), 2)
            self.assertEqual(docker.calls.count("sqlite"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
