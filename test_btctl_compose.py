import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from btctl_compose import ComposeAdopter, ComposeInstaller, InstallError, render_compose
from btctl_core import (
    ConfigError,
    DeploymentPlan,
    InstallConfig,
    OperationLock,
    ReleaseIdentity,
    StateStore,
)


def _compose_interpolate(value: str) -> str:
    """Model Compose's dollar expansion for generated string values."""
    sentinel = "\0COMPOSE_LITERAL_DOLLAR\0"
    return os.path.expandvars(value.replace("$$", sentinel)).replace(sentinel, "$")


class FakeDocker:
    def __init__(self, *, fail_health=False, fail_probe=False, fail_up=False):
        self.calls = []
        self.fail_health = fail_health
        self.fail_probe = fail_probe
        self.fail_up = fail_up
        self.images = {}
        self.networks = {
            "cwa_default": {"Id": "network-cwa"},
            "authentik_backend": {"Id": "network-edge"},
        }
        self.containers = {
            "calibre-web-automated": {
                "Id": "cwa-id",
                "State": {"Status": "running"},
                "NetworkSettings": {"Networks": {"cwa_default": {}}},
                "Config": {"Image": "crocodilestick/calibre-web-automated:v4.0.6"},
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

    def prepare_data_directory(self, image, path):
        self.calls.append(("prepare_data_directory", image, str(path)))
        Path(path).chmod(0o2750)

    def compose_validate(self, document, project):
        self.calls.append(("compose_validate", str(document), project))
        json.loads(Path(document).read_text(encoding="utf-8"))

    def compose_up(self, document, project):
        self.calls.append(("compose_up", str(document), project))
        payload = json.loads(Path(document).read_text(encoding="utf-8"))
        self.networks[payload["networks"]["private"]["name"]] = {
            "Id": "private-id",
            "Labels": payload["networks"]["private"]["labels"],
            "Internal": payload["networks"]["private"]["internal"],
        }
        if self.fail_up:
            raise InstallError("compose up failed after creating the private network")
        for service in payload["services"].values():
            name = service["container_name"]
            role = service["environment"]["BT_ROLE"]
            ports = {}
            if service.get("ports"):
                ports["8080/tcp"] = [
                    {
                        "HostIp": "",
                        "HostPort": str(service["ports"][0]["published"]),
                    }
                ]
            memory = 1024 * 1024 * 1024 if role == "api" else 128 * 1024 * 1024
            nano_cpus = 2_000_000_000 if role == "api" else 500_000_000
            mounts = [
                {
                    "Type": volume["type"],
                    "Source": _compose_interpolate(volume["source"]),
                    "Destination": volume["target"],
                    "RW": True,
                }
                for volume in service.get("volumes", [])
            ]
            self.containers[name] = {
                "Id": f"{name}-id",
                "Image": "sha256:image-id",
                "State": {"Status": "running", "Health": {"Status": "healthy"}},
                "Config": {
                    "Image": service["image"],
                    "Labels": service["labels"],
                    "Env": [
                        f"{key}={_compose_interpolate(value)}"
                        for key, value in service["environment"].items()
                    ],
                    "User": service["user"],
                },
                "HostConfig": {
                    "PortBindings": ports,
                    "ReadonlyRootfs": service["read_only"],
                    "Privileged": service["privileged"],
                    "CapDrop": service["cap_drop"],
                    "CapAdd": None,
                    "SecurityOpt": service["security_opt"],
                    "Tmpfs": {
                        "/tmp": service["tmpfs"][0].split(":", 1)[1]
                    },
                    "PidsLimit": service["pids_limit"],
                    "Memory": memory,
                    "NanoCpus": nano_cpus,
                    "RestartPolicy": {
                        "Name": service["restart"],
                        "MaximumRetryCount": 0,
                    },
                },
                "Mounts": mounts,
                "NetworkSettings": {
                    "Networks": {
                        payload["networks"][key]["name"]: {
                            "Aliases": (
                                service["networks"][key].get("aliases", [])
                                if isinstance(service["networks"][key], dict)
                                else []
                            )
                        }
                        for key in service["networks"]
                    }
                },
            }

    def wait_healthy(self, names, timeout_seconds):
        self.calls.append(("wait_healthy", tuple(names), timeout_seconds))
        if self.fail_health:
            raise InstallError("health check failed")

    def probe_http(self, container, url):
        self.calls.append(("probe_http", container, url))
        if self.fail_probe:
            raise InstallError("runtime dependency probe failed")

    def probe_auth(self, container, url):
        self.calls.append(("probe_auth", container, url))
        if self.fail_probe:
            raise InstallError("runtime authentication probe failed")

    def probe_sqlite(self, container, database_path):
        self.calls.append(("probe_sqlite", container, database_path))
        if self.fail_probe:
            raise InstallError("runtime SQLite probe failed")

    def compose_down(self, document, project):
        self.calls.append(("compose_down", str(document), project))
        payload = json.loads(Path(document).read_text(encoding="utf-8"))
        for service in payload["services"].values():
            self.containers.pop(service["container_name"], None)
        self.networks.pop(payload["networks"]["private"]["name"], None)


def values(root: Path, *, forwarded=False):
    result = {
        "BT_INSTALL_PROFILE": "compose-existing",
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
        "LLM_PROVIDER": "local",
        "LLM_MODEL": "gemma4-12b",
        "BT_LOCAL_URL": "http://host.docker.internal:2819/v1/chat/completions",
        "LLM_API_KEY": "",
    }
    if forwarded:
        result.update(
            {
                "BT_INGRESS_MODE": "docker-edge",
                "BT_PROXY_PORT": "",
                "BT_EDGE_NETWORK": "authentik_backend",
                "BT_AUTH_PROFILE": "authentik-forwarded",
                "BT_IDENTITY_PROXY_IP": "172.30.50.9/32",
                "BT_AUTHENTIK_VERSION": "2026.5.4",
                "BT_AUTHENTIK_OUTPOST_URL": "http://authentik-outpost:9000",
                "BT_REVERSE_PROXY": "nginx",
            }
        )
    return result


class ComposeRenderTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="d" * 40, clean=True
        )

    def test_normal_profile_has_two_hardened_roles_and_only_proxy_port(self):
        with tempfile.TemporaryDirectory() as directory:
            config = InstallConfig.from_mapping(values(Path(directory)), self.identity)
            plan = DeploymentPlan.from_config(config)

            document = render_compose(config, plan, "install-id")

            self.assertEqual(set(document["services"]), {"api", "proxy"})
            api = document["services"]["api"]
            proxy = document["services"]["proxy"]
            self.assertEqual(api["image"], proxy["image"])
            self.assertEqual(api["image"], self.identity.image)
            self.assertNotIn("ports", api)
            self.assertEqual(proxy["ports"], [{"target": 8080, "published": 8385, "protocol": "tcp"}])
            self.assertEqual(set(api["networks"]), {"private", "cwa"})
            self.assertEqual(set(proxy["networks"]), {"private", "cwa"})
            self.assertTrue(api["read_only"])
            self.assertEqual(api["user"], "101:102")
            self.assertFalse(api["privileged"])
            self.assertEqual(api["labels"]["io.cwa-translate.role"], "api")
            self.assertNotIn("latest", json.dumps(document))
            self.assertNotIn("calibre-web", document["services"])

    def test_forwarded_profile_joins_identity_edge_without_publishing_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            config = InstallConfig.from_mapping(
                values(Path(directory), forwarded=True), self.identity
            )
            plan = DeploymentPlan.from_config(config)

            document = render_compose(config, plan, "install-id")

            self.assertNotIn("ports", document["services"]["api"])
            self.assertNotIn("ports", document["services"]["proxy"])
            self.assertEqual(
                set(document["services"]["api"]["networks"]), {"private", "edge"}
            )
            self.assertEqual(
                set(document["services"]["proxy"]["networks"]),
                {"private", "cwa", "edge"},
            )
            self.assertTrue(document["networks"]["edge"]["external"])

    def test_compose_escapes_literal_dollars_without_changing_runtime_value(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = values(Path(directory))
            configured.update(
                {
                    "LLM_PROVIDER": "openai",
                    "BT_LOCAL_URL": "",
                    "LLM_API_KEY": "secret$HOME$$literal",
                    "BT_DATA_DIR": str(Path(directory) / "$HOME-data"),
                }
            )
            config = InstallConfig.from_mapping(configured, self.identity)
            plan = DeploymentPlan.from_config(config)
            document = render_compose(config, plan, "install-id")

            self.assertEqual(
                document["services"]["api"]["environment"]["LLM_API_KEY"],
                "secret$$HOME$$$$literal",
            )
            self.assertEqual(
                document["services"]["api"]["volumes"][0]["source"],
                str(Path(directory) / "$$HOME-data"),
            )

            docker = FakeDocker()
            state = ComposeInstaller(docker).install(config, plan, Path(directory))
            self.assertEqual(state.status, "installed")
            api = docker.containers[str(plan.resources["api"]["name"])]
            self.assertIn("LLM_API_KEY=secret$HOME$$literal", api["Config"]["Env"])
            self.assertEqual(api["Mounts"][0]["Source"], config.data_dir)


class ComposeInstallTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="e" * 40, clean=True
        )

    def test_install_preflights_builds_starts_verifies_then_writes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            with mock.patch(
                "btctl_compose.os.chmod", wraps=os.chmod
            ) as chmod:
                state = ComposeInstaller(docker).install(config, plan, root)

            self.assertEqual(state.image, self.identity.image)
            self.assertNotIn(
                mock.call(Path(config.data_dir), 0o700),
                chmod.call_args_list,
            )
            self.assertEqual(StateStore(root / "state").load(), state)
            call_names = [call[0] for call in docker.calls]
            self.assertLess(call_names.index("require_available"), call_names.index("build_image"))
            self.assertLess(call_names.index("build_image"), call_names.index("prepare_data_directory"))
            self.assertLess(call_names.index("prepare_data_directory"), call_names.index("compose_up"))
            self.assertLess(call_names.index("build_image"), call_names.index("compose_up"))
            self.assertLess(call_names.index("compose_up"), call_names.index("wait_healthy"))
            self.assertLess(call_names.index("wait_healthy"), call_names.index("probe_http"))
            self.assertLess(call_names.index("probe_http"), call_names.index("probe_auth"))
            self.assertLess(call_names.index("probe_auth"), call_names.index("probe_sqlite"))
            self.assertEqual(state.resources["api"]["id"], "cwa-translate-test-api-id")
            self.assertEqual(state.resources["proxy"]["id"], "cwa-translate-test-proxy-id")
            self.assertEqual(os.stat(root / "state" / "deployment.compose.json").st_mode & 0o777, 0o600)

    def test_concurrent_lifecycle_operation_stops_before_docker_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            with OperationLock(Path(config.state_dir)):
                with self.assertRaisesRegex(ConfigError, "already in progress"):
                    ComposeInstaller(docker).install(config, plan, root)

            self.assertEqual(docker.calls, [])

    def test_forwarded_install_writes_the_exact_private_identity_edge_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(
                values(root, forwarded=True), self.identity
            )
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            state = ComposeInstaller(docker).install(config, plan, root)

            artifact = root / "state" / "authentik-edge.nginx.conf"
            self.assertTrue(artifact.is_file())
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("proxy_set_header Cookie \"\";", content)
            self.assertIn("X-authentik-uid $bt_authentik_uid", content)
            self.assertIn("sha256", state.resources["identity_edge_config"])

    def test_failed_health_removes_owned_runtime_and_never_writes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker(fail_health=True)

            with self.assertRaisesRegex(InstallError, "health"):
                ComposeInstaller(docker).install(config, plan, root)

            self.assertIn("compose_down", [call[0] for call in docker.calls])
            self.assertFalse((root / "state" / "state.json").exists())

    def test_partial_compose_up_failure_removes_created_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker(fail_up=True)

            with self.assertRaisesRegex(InstallError, "compose up failed"):
                ComposeInstaller(docker).install(config, plan, root)

            private_name = str(plan.resources["private_network"]["name"])
            self.assertIn("compose_down", [call[0] for call in docker.calls])
            self.assertNotIn(private_name, docker.networks)
            self.assertFalse((root / "state" / "state.json").exists())

    def test_failed_live_dependency_probe_removes_runtime_and_never_writes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker(fail_probe=True)

            with self.assertRaisesRegex(InstallError, "dependency probe"):
                ComposeInstaller(docker).install(config, plan, root)

            calls = [call[0] for call in docker.calls]
            self.assertIn("compose_down", calls)
            self.assertFalse((root / "state" / "state.json").exists())

    def test_preflight_failure_has_no_build_or_runtime_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            docker.containers["cwa-translate-test-api"] = {"Id": "collision"}

            with self.assertRaisesRegex(InstallError, "already exists"):
                ComposeInstaller(docker).install(config, plan, root)

            self.assertNotIn("build_image", [call[0] for call in docker.calls])
            self.assertNotIn("compose_up", [call[0] for call in docker.calls])
            self.assertFalse((root / "state").exists())

    def test_preflight_rejects_cwa_version_without_exact_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            docker.containers["calibre-web-automated"]["Config"]["Image"] = (
                "crocodilestick/calibre-web-automated:latest"
            )

            with self.assertRaisesRegex(InstallError, "CWA version"):
                ComposeInstaller(docker).install(config, plan, root)

            self.assertNotIn("build_image", [call[0] for call in docker.calls])

    def test_fresh_install_rejects_a_nonempty_data_directory_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            marker = data / "belongs-to-another-app"
            marker.write_text("preserve", encoding="utf-8")
            before = marker.stat()
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            with self.assertRaisesRegex(InstallError, "empty for a fresh install"):
                ComposeInstaller(docker).install(config, plan, root)

            after = marker.stat()
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertEqual((after.st_uid, after.st_gid, after.st_mode), (before.st_uid, before.st_gid, before.st_mode))
            self.assertNotIn("build_image", [call[0] for call in docker.calls])


class ComposeAdoptTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="a" * 40, clean=True
        )

    def test_adopt_recovers_labeled_split_runtime_without_docker_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            ComposeInstaller(docker).install(config, plan, root)
            (root / "state" / "state.json").unlink()
            before = len(docker.calls)

            state = ComposeAdopter(docker).adopt(config, plan)

            new_calls = docker.calls[before:]
            self.assertFalse(
                {"build_image", "compose_up", "compose_down"}
                & {call[0] for call in new_calls}
            )
            self.assertEqual(state.status, "adopted")
            self.assertEqual(state.resources["api"]["ownership"], "owned")
            self.assertEqual(state.resources["proxy"]["ownership"], "owned")
            self.assertEqual(state.resources["private_network"]["ownership"], "owned")
            self.assertEqual(StateStore(root / "state").load(), state)

    def test_adopt_rejects_unlabeled_runtime_without_writing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            docker.containers["cwa-translate-test-api"] = {
                "Id": "api-id",
                "Config": {"Labels": {}},
            }
            docker.containers["cwa-translate-test-proxy"] = {
                "Id": "proxy-id",
                "Config": {"Labels": {}},
            }

            with self.assertRaisesRegex(InstallError, "ownership labels"):
                ComposeAdopter(docker).adopt(config, plan)

            self.assertFalse((root / "state" / "state.json").exists())

    def test_adopt_routes_exact_combined_v214_to_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            docker.containers[config.install_name] = {
                "Id": "legacy-id",
                "Config": {"Image": "local/book-translator:2.1.4"},
            }

            with self.assertRaisesRegex(InstallError, "upgrade"):
                ComposeAdopter(docker).adopt(config, plan)

    def test_adopt_rejects_disabled_auth_even_when_labels_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            ComposeInstaller(docker).install(config, plan, root)
            (root / "state" / "state.json").unlink()
            api = docker.containers["cwa-translate-test-api"]
            api["Config"]["Env"] = [
                item for item in api["Config"]["Env"] if not item.startswith("BT_AUTH_MODE=")
            ] + ["BT_AUTH_MODE=disabled", "BT_ALLOW_INSECURE_AUTH=true"]

            with self.assertRaisesRegex(InstallError, "runtime environment"):
                ComposeAdopter(docker).adopt(config, plan)

    def test_adopt_rejects_privileged_runtime_even_when_labels_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            ComposeInstaller(docker).install(config, plan, root)
            (root / "state" / "state.json").unlink()
            docker.containers[plan.resources["api"]["name"]]["HostConfig"][
                "Privileged"
            ] = True

            with self.assertRaisesRegex(InstallError, "sandbox"):
                ComposeAdopter(docker).adopt(config, plan)

            self.assertFalse((root / "state" / "state.json").exists())

    def test_adopt_rejects_wrong_api_data_bind_even_when_labels_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            ComposeInstaller(docker).install(config, plan, root)
            (root / "state" / "state.json").unlink()
            docker.containers[plan.resources["api"]["name"]]["Mounts"][0][
                "Source"
            ] = str(root / "other-data")

            with self.assertRaisesRegex(InstallError, "data bind"):
                ComposeAdopter(docker).adopt(config, plan)

            self.assertFalse((root / "state" / "state.json").exists())

    def test_adopt_rejects_a_non_internal_private_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            ComposeInstaller(docker).install(config, plan, root)
            (root / "state" / "state.json").unlink()
            docker.networks[plan.resources["private_network"]["name"]][
                "Internal"
            ] = False

            with self.assertRaisesRegex(InstallError, "isolation"):
                ComposeAdopter(docker).adopt(config, plan)

            self.assertFalse((root / "state" / "state.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
