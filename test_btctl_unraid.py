import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from btctl_core import DeploymentPlan, InstallConfig, ReleaseIdentity, StateStore
from btctl_docker import DockerCLI
from btctl_unraid import (
    ContainerSpec,
    InstallError,
    UnraidAdopter,
    UnraidInstaller,
    render_templates,
)


def values(root: Path):
    return {
        "BT_INSTALL_PROFILE": "unraid",
        "BT_INSTALL_NAME": "cwa-translate-test",
        "BT_INGRESS_MODE": "published",
        "BT_PROXY_PORT": "8385",
        "BT_AUTH_PROFILE": "cwa-session",
        "BT_PUBLIC_ORIGIN": "https://books.example.test",
        "CWA_UPSTREAM": "http://calibre-web-automated:8083",
        "BT_CWA_CONTAINER": "calibre-web-automated",
        "BT_CWA_NETWORK": "cwa_default",
        "BT_CWA_VERSION": "4.0.6",
        "BT_STATE_DIR": str(root / "state"),
        "BT_DATA_DIR": str(root / "data"),
        "BT_BACKUP_DIR": str(root / "backups"),
        "BT_UNRAID_TEMPLATE_DIR": str(root / "templates-user"),
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-4.1-mini",
        "BT_LOCAL_URL": "",
        "LLM_API_KEY": "do-not-copy-to-xml",
    }


class FakeDocker:
    def __init__(self, *, fail_proxy_health=False):
        self.calls = []
        self.fail_proxy_health = fail_proxy_health
        self.images = {}
        self.networks = {"cwa_default": {"Id": "cwa-network"}}
        self.containers = {
            "calibre-web-automated": {
                "Id": "cwa-id",
                "State": {"Status": "running"},
                "Config": {"Image": "crocodilestick/calibre-web-automated:v4.0.6"},
                "NetworkSettings": {"Networks": {"cwa_default": {}}},
            }
        }

    def require_available(self):
        self.calls.append(("require_available",))

    def inspect_container(self, name):
        self.calls.append(("inspect_container", name))
        return self.containers.get(name)

    def inspect_network(self, name):
        self.calls.append(("inspect_network", name))
        return self.networks.get(name)

    def inspect_image(self, name):
        self.calls.append(("inspect_image", name))
        return self.images.get(name)

    def build_image(self, repository, image, labels):
        self.calls.append(("build_image", str(repository), image, dict(labels)))
        self.images[image] = {"Id": "sha256:image-id", "Config": {"Labels": labels}}

    def create_network(self, name, labels, *, internal):
        self.calls.append(("create_network", name, dict(labels), internal))
        self.networks[name] = {"Id": "private-id", "Labels": dict(labels)}

    def create_container(self, spec):
        self.calls.append(("create_container", spec))
        bindings = {}
        if spec.publish_port is not None:
            bindings = {"8080/tcp": [{"HostPort": str(spec.publish_port)}]}
        self.containers[spec.name] = {
            "Id": f"{spec.name}-id",
            "Image": "sha256:image-id",
            "State": {"Status": "created", "Health": {"Status": "starting"}},
            "Config": {
                "Image": spec.image,
                "Labels": dict(spec.labels),
                "Env": spec.env_file.read_text(encoding="utf-8").splitlines(),
            },
            "HostConfig": {"PortBindings": bindings},
            "NetworkSettings": {"Networks": {spec.primary_network: {}}},
        }

    def connect_network(self, network, container):
        self.calls.append(("connect_network", network, container))
        self.containers[container]["NetworkSettings"]["Networks"][network] = {}

    def start_container(self, name):
        self.calls.append(("start_container", name))
        self.containers[name]["State"] = {
            "Status": "running",
            "Health": {"Status": "healthy"},
        }

    def wait_healthy(self, names, timeout_seconds):
        self.calls.append(("wait_healthy", tuple(names), timeout_seconds))
        if self.fail_proxy_health and any(name.endswith("-proxy") for name in names):
            raise InstallError("proxy health failed")

    def remove_container(self, name):
        self.calls.append(("remove_container", name))
        self.containers.pop(name, None)

    def remove_network(self, name):
        self.calls.append(("remove_network", name))
        self.networks.pop(name, None)


class UnraidTemplateTests(unittest.TestCase):
    def test_templates_are_parseable_immutable_and_never_publish_api_or_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = ReleaseIdentity.from_checkout(
                version="2.2.0", sha="b" * 40, clean=True
            )
            config = InstallConfig.from_mapping(values(root), identity)
            plan = DeploymentPlan.from_config(config)

            templates = render_templates(config, plan)

            self.assertEqual(set(templates), {"api", "proxy"})
            api = ET.fromstring(templates["api"])
            proxy = ET.fromstring(templates["proxy"])
            self.assertEqual(api.findtext("Repository"), identity.image)
            self.assertEqual(proxy.findtext("Repository"), identity.image)
            self.assertFalse(
                [item for item in api.findall("Config") if item.get("Type") == "Port"]
            )
            self.assertEqual(
                len([item for item in proxy.findall("Config") if item.get("Type") == "Port"]),
                1,
            )
            encoded = json.dumps(templates)
            self.assertNotIn("latest", encoded)
            self.assertNotIn("do-not-copy-to-xml", encoded)
            self.assertIn("managed by btctl", encoded)


class DockerCLIContractTests(unittest.TestCase):
    def test_raw_create_passes_private_env_file_without_secret_or_shell(self):
        spec = ContainerSpec(
            role="api",
            name="cwa-translate-api",
            image="local/cwa-translate:2.2.0-abcdef012345",
            env_file=Path("/private/state/api.env"),
            labels={"io.cwa-translate.role": "api"},
            primary_network="cwa-translate-private",
            network_alias="translator-api",
            data_dir=Path("/mnt/user/appdata/cwa-translate/data"),
            publish_port=None,
        )
        completed = mock.Mock(returncode=0, stdout="container-id\n", stderr="")

        with mock.patch("subprocess.run", return_value=completed) as run:
            DockerCLI().create_container(spec)

        arguments = run.call_args.args[0]
        self.assertIsInstance(arguments, list)
        self.assertEqual(arguments[0], "docker")
        self.assertIn("--env-file", arguments)
        self.assertIn("/private/state/api.env", arguments)
        self.assertNotIn("--publish", arguments)
        self.assertNotIn("LLM_API_KEY", " ".join(arguments))
        self.assertNotIn("shell", run.call_args.kwargs)


class UnraidInstallTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="c" * 40, clean=True
        )

    def test_install_uses_two_raw_containers_and_commits_verified_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            state = UnraidInstaller(docker, prepare_data=lambda path: path.mkdir()).install(
                config, plan, root
            )

            specs = [call[1] for call in docker.calls if call[0] == "create_container"]
            self.assertEqual({spec.role for spec in specs}, {"api", "proxy"})
            api = next(spec for spec in specs if spec.role == "api")
            proxy = next(spec for spec in specs if spec.role == "proxy")
            self.assertIsNone(api.publish_port)
            self.assertEqual(proxy.publish_port, 8385)
            self.assertEqual(api.image, proxy.image)
            self.assertEqual(api.image, self.identity.image)
            self.assertEqual(os.stat(root / "state" / "api.env").st_mode & 0o777, 0o600)
            self.assertNotIn("do-not-copy-to-xml", (root / "state" / "state.json").read_text())
            self.assertEqual(StateStore(root / "state").load(), state)
            self.assertTrue((root / "templates-user" / "my-cwa-translate-api.xml").is_file())
            self.assertTrue((root / "templates-user" / "my-cwa-translate-proxy.xml").is_file())

    def test_failure_removes_only_created_roles_and_private_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker(fail_proxy_health=True)

            with self.assertRaisesRegex(InstallError, "health"):
                UnraidInstaller(docker, prepare_data=lambda path: path.mkdir()).install(
                    config, plan, root
                )

            removed = [call[1] for call in docker.calls if call[0].startswith("remove_")]
            self.assertEqual(
                removed,
                ["cwa-translate-test-proxy", "cwa-translate-test-api", "cwa-translate-test-private"],
            )
            self.assertIn("calibre-web-automated", docker.containers)
            self.assertFalse((root / "state" / "state.json").exists())

    def test_template_collision_stops_before_image_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            template_dir = root / "templates-user"
            template_dir.mkdir()
            (template_dir / "my-cwa-translate-api.xml").write_text("preserve")
            docker = FakeDocker()

            with self.assertRaisesRegex(InstallError, "template"):
                UnraidInstaller(docker, prepare_data=lambda path: path.mkdir()).install(
                    config, plan, root
                )

            self.assertNotIn("build_image", [call[0] for call in docker.calls])
            self.assertEqual(
                (template_dir / "my-cwa-translate-api.xml").read_text(), "preserve"
            )


class UnraidAdoptTests(unittest.TestCase):
    def test_adopt_recovers_btctl_runtime_without_docker_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = ReleaseIdentity.from_checkout(
                version="2.2.0", sha="d" * 40, clean=True
            )
            config = InstallConfig.from_mapping(values(root), identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            UnraidInstaller(docker, prepare_data=lambda path: path.mkdir()).install(
                config, plan, root
            )
            (root / "state" / "state.json").unlink()
            before = len(docker.calls)

            state = UnraidAdopter(docker).adopt(config, plan)

            self.assertEqual(state.status, "adopted")
            self.assertFalse(
                {"build_image", "create_network", "create_container", "start_container"}
                & {call[0] for call in docker.calls[before:]}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
