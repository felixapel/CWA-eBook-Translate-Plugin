import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from btctl_core import DeploymentPlan, InstallConfig, ReleaseIdentity, StateStore
from btctl_lifecycle import RuntimeUninstaller
from btctl_docker import DockerCLI, DockerCommandError
from btctl_unraid import (
    ContainerSpec,
    InstallError,
    UnraidAdopter,
    UnraidInstaller,
    prepare_data_directory,
    render_templates,
)


def values(root: Path, *, forwarded=False):
    result = {
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
    if forwarded:
        result.update({
            "BT_INGRESS_MODE": "docker-edge",
            "BT_PROXY_PORT": "",
            "BT_EDGE_NETWORK": "authentik_backend",
            "BT_AUTH_PROFILE": "authentik-forwarded",
            "BT_IDENTITY_PROXY_IP": "172.30.50.9/32",
            "BT_AUTHENTIK_VERSION": "2026.5.4",
            "BT_AUTHENTIK_OUTPOST_URL": "http://authentik-outpost:9000",
            "BT_REVERSE_PROXY": "caddy",
        })
    return result


class FakeDocker:
    def __init__(
        self,
        *,
        fail_proxy_health=False,
        fail_network_create_after_effect=False,
        fail_create_role_after_effect=None,
    ):
        self.calls = []
        self.fail_proxy_health = fail_proxy_health
        self.fail_network_create_after_effect = fail_network_create_after_effect
        self.fail_create_role_after_effect = fail_create_role_after_effect
        self.images = {}
        self.networks = {
            "cwa_default": {"Id": "cwa-network"},
            "authentik_backend": {"Id": "edge-network"},
        }
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
        self.networks[name] = {
            "Id": "private-id",
            "Labels": dict(labels),
            "Internal": internal,
        }
        if self.fail_network_create_after_effect:
            raise InstallError("network create response was lost")

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
                "User": "101:102",
            },
            "HostConfig": {
                "PortBindings": bindings,
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {
                    "/tmp": "rw,noexec,nosuid,size=64m,uid=101,gid=102,mode=700"
                },
                "PidsLimit": 256 if spec.role == "api" else 64,
                "Memory": (
                    1024 * 1024 * 1024 if spec.role == "api" else 128 * 1024 * 1024
                ),
                "NanoCpus": 2_000_000_000 if spec.role == "api" else 500_000_000,
                "RestartPolicy": {
                    "Name": "unless-stopped",
                    "MaximumRetryCount": 0,
                },
            },
            "Mounts": (
                [
                    {
                        "Type": "bind",
                        "Source": str(spec.data_dir),
                        "Destination": "/app/data",
                        "RW": True,
                    }
                ]
                if spec.data_dir is not None
                else []
            ),
            "NetworkSettings": {
                "Networks": {
                    spec.primary_network: {
                        "Aliases": [spec.network_alias] if spec.network_alias else []
                    }
                }
            },
        }
        if self.fail_create_role_after_effect == spec.role:
            raise InstallError(f"{spec.role} create response was lost")

    def connect_network(self, network, container):
        self.calls.append(("connect_network", network, container))
        self.containers[container]["NetworkSettings"]["Networks"][network] = {}

    def start_container(self, name):
        self.calls.append(("start_container", name))
        self.containers[name]["State"] = {
            "Status": "running",
            "Health": {"Status": "healthy"},
        }

    def stop_container(self, name):
        self.calls.append(("stop_container", name))
        self.containers[name]["State"]["Status"] = "exited"

    def wait_healthy(self, names, timeout_seconds):
        self.calls.append(("wait_healthy", tuple(names), timeout_seconds))
        if self.fail_proxy_health and any(name.endswith("-proxy") for name in names):
            raise InstallError("proxy health failed")

    def probe_http(self, container, url):
        self.calls.append(("probe_http", container, url))

    def probe_auth(self, container, url):
        self.calls.append(("probe_auth", container, url))

    def probe_sqlite(self, container, database_path):
        self.calls.append(("probe_sqlite", container, database_path))

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
    def test_inspect_distinguishes_verified_absence_from_docker_failure(self):
        missing = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Error: No such container: cwa-translate-api\n",
        )
        unavailable = mock.Mock(
            returncode=1,
            stdout="",
            stderr="permission denied while trying to connect to the Docker daemon\n",
        )

        with mock.patch("subprocess.run", return_value=missing):
            self.assertIsNone(DockerCLI().inspect_container("cwa-translate-api"))
        with mock.patch("subprocess.run", return_value=unavailable):
            with self.assertRaisesRegex(DockerCommandError, "inspect failed"):
                DockerCLI().inspect_container("cwa-translate-api")

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
        self.assertIn("--user", arguments)
        self.assertIn("101:102", arguments)
        self.assertIn("/private/state/api.env", arguments)
        self.assertNotIn("--publish", arguments)
        self.assertNotIn("LLM_API_KEY", " ".join(arguments))
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_compose_data_preparation_preserves_private_operator_group_access(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", return_value=completed) as run, mock.patch(
            "btctl_docker.os.getgid", return_value=4242, create=True
        ):
            DockerCLI().prepare_data_directory(
                "local/cwa-translate:2.2.0-abcdef012345",
                Path("/srv/cwa-translate/data"),
            )

        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:3], ["docker", "run", "--rm"])
        self.assertIn("--user", arguments)
        self.assertIn("0:0", arguments)
        self.assertIn("type=bind,src=/srv/cwa-translate/data,dst=/data", arguments)
        self.assertIn("local/cwa-translate:2.2.0-abcdef012345", arguments)
        self.assertNotIn("shell", run.call_args.kwargs)
        script = arguments[-1]
        self.assertIn("find /data -xdev ! -type d ! -type f", script)
        self.assertIn("find /data -xdev -type d -exec chown 101:4242", script)
        self.assertIn("find /data -xdev -type f -exec chown 101:4242", script)
        self.assertIn("find /data -xdev -type d -exec chmod 2750", script)
        self.assertIn("find /data -xdev -type f -exec chmod 0640", script)

    def test_legacy_data_preparation_preserves_owner_and_grants_operator_checkpoint_access(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", return_value=completed) as run, mock.patch(
            "btctl_docker.os.getgid", return_value=4242, create=True
        ):
            DockerCLI().prepare_migration_source(
                "sha256:legacy-image-id",
                Path("/srv/cwa-translate/legacy-data"),
            )

        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:3], ["docker", "run", "--rm"])
        self.assertIn("sha256:legacy-image-id", arguments)
        self.assertIn(
            "type=bind,src=/srv/cwa-translate/legacy-data,dst=/data",
            arguments,
        )
        script = arguments[-1]
        self.assertIn("find /data -xdev -type d -exec chgrp 4242", script)
        self.assertIn("find /data -xdev -type f -exec chgrp 4242", script)
        self.assertIn("find /data -xdev -type d -exec chmod 2770", script)
        self.assertIn("find /data -xdev -type f -exec chmod 0660", script)
        self.assertNotIn("chown", script)

    def test_runtime_probes_use_exact_container_exec_without_a_host_shell(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", return_value=completed) as run:
            docker = DockerCLI()
            docker.probe_http(
                "cwa-translate-proxy",
                "http://calibre-web-automated:8083/",
            )
            http_arguments = run.call_args.args[0]
            docker.probe_auth(
                "cwa-translate-api",
                "http://calibre-web-automated:8083/ajax/emailstat",
            )
            auth_arguments = run.call_args.args[0]
            docker.probe_sqlite(
                "cwa-translate-api",
                "/app/data/translations.db",
            )
            sqlite_arguments = run.call_args.args[0]

        self.assertEqual(http_arguments[:3], ["docker", "exec", "cwa-translate-proxy"])
        self.assertIn("http://calibre-web-automated:8083/", http_arguments)
        self.assertEqual(auth_arguments[:3], ["docker", "exec", "cwa-translate-api"])
        self.assertIn("code in (401,403)", auth_arguments[-2])
        self.assertEqual(sqlite_arguments[:3], ["docker", "exec", "cwa-translate-api"])
        self.assertIn("/app/data/translations.db", sqlite_arguments)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_image_version_probe_uses_immutable_networkless_sandbox(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", return_value=completed) as run:
            DockerCLI().probe_image_version("sha256:legacy", "2.1.4")

        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:3], ["docker", "run", "--rm"])
        self.assertIn("--network", arguments)
        self.assertIn("none", arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("ALL", arguments)
        self.assertIn("no-new-privileges:true", arguments)
        self.assertIn("sha256:legacy", arguments)
        self.assertEqual(arguments[-1], "2.1.4")

    def test_image_build_uses_an_immutable_git_archive_as_stdin(self):
        archive = mock.Mock(returncode=0, stdout=b"tar-bytes", stderr=b"")
        built = mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch("subprocess.run", side_effect=[archive, built]) as run:
            DockerCLI().build_image(
                Path("/checkout"),
                "local/cwa-translate:2.2.0-abcdef012345",
                {"io.cwa-translate.revision": "a" * 40},
            )

        archive_arguments = run.call_args_list[0].args[0]
        build_arguments = run.call_args_list[1].args[0]
        self.assertEqual(archive_arguments[0], "git")
        self.assertIn("core.fsmonitor=false", archive_arguments)
        self.assertIn("core.untrackedCache=false", archive_arguments)
        self.assertEqual(
            archive_arguments[-5:],
            ["-C", "/checkout", "archive", "--format=tar", "a" * 40],
        )
        self.assertEqual(
            run.call_args_list[0].kwargs["env"]["GIT_NO_REPLACE_OBJECTS"],
            "1",
        )
        self.assertEqual(build_arguments[0:2], ["docker", "build"])
        self.assertEqual(build_arguments[-1], "-")
        self.assertEqual(run.call_args_list[1].kwargs["input"], b"tar-bytes")
        self.assertNotIn("/checkout", build_arguments)


class UnraidDataPreparationTests(unittest.TestCase):
    def test_root_hardens_the_complete_existing_tree_for_the_runtime_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            nested = data / "cache"
            nested.mkdir(parents=True)
            database = data / "translations.db"
            database.write_text("database", encoding="utf-8")
            cached = nested / "entry.json"
            cached.write_text("cache", encoding="utf-8")

            with (
                mock.patch("btctl_unraid.os.geteuid", return_value=0),
                mock.patch("btctl_unraid.os.chown") as chown,
            ):
                prepare_data_directory(data)

            self.assertEqual(
                {call.args for call in chown.call_args_list},
                {
                    (data, 101, 102),
                    (nested, 101, 102),
                    (database, 101, 102),
                    (cached, 101, 102),
                },
            )
            self.assertEqual(data.stat().st_mode & 0o777, 0o700)
            self.assertEqual(nested.stat().st_mode & 0o777, 0o700)
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cached.stat().st_mode & 0o777, 0o600)

    def test_invalid_descendant_stops_before_any_ownership_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (data / "escape").symlink_to(root / "outside")

            with (
                mock.patch("btctl_unraid.os.geteuid", return_value=0),
                mock.patch("btctl_unraid.os.chown") as chown,
                self.assertRaisesRegex(InstallError, "only regular files and directories"),
            ):
                prepare_data_directory(data)

            chown.assert_not_called()


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

    def test_ambiguous_network_create_cleans_only_exact_labeled_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker(fail_network_create_after_effect=True)

            with self.assertRaisesRegex(InstallError, "response was lost"):
                UnraidInstaller(
                    docker, prepare_data=lambda path: path.mkdir()
                ).install(config, plan, root)

            self.assertNotIn(plan.resources["private_network"]["name"], docker.networks)
            self.assertFalse((root / "state" / "state.json").exists())

    def test_ambiguous_container_create_cleans_exact_attempted_runtime(self):
        for failed_role in ("api", "proxy"):
            with self.subTest(failed_role=failed_role), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = InstallConfig.from_mapping(values(root), self.identity)
                plan = DeploymentPlan.from_config(config)
                docker = FakeDocker(fail_create_role_after_effect=failed_role)

                with self.assertRaisesRegex(InstallError, "response was lost"):
                    UnraidInstaller(
                        docker, prepare_data=lambda path: path.mkdir()
                    ).install(config, plan, root)

                for role in ("api", "proxy"):
                    self.assertNotIn(plan.resources[role]["name"], docker.containers)
                self.assertNotIn(
                    plan.resources["private_network"]["name"], docker.networks
                )
                self.assertFalse((root / "state" / "state.json").exists())

    def test_forwarded_install_writes_a_private_caddy_identity_edge_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(
                values(root, forwarded=True), self.identity
            )
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            state = UnraidInstaller(
                docker, prepare_data=lambda path: path.mkdir()
            ).install(config, plan, root)

            artifact = root / "state" / "authentik-edge.caddy"
            self.assertTrue(artifact.is_file())
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            self.assertIn("request_header -Cookie", artifact.read_text())
            self.assertIn("sha256", state.resources["identity_edge_config"])

    def test_reinstall_after_managed_uninstall_archives_old_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()
            installer = UnraidInstaller(
                docker, prepare_data=lambda path: path.mkdir(exist_ok=True)
            )
            original = installer.install(config, plan, root)
            RuntimeUninstaller(docker).uninstall(config, plan)

            replacement = installer.install(config, plan, root)

            self.assertNotEqual(replacement.install_id, original.install_id)
            history = (
                root
                / "state"
                / "history"
                / f"{original.install_id}-uninstalled.json"
            )
            self.assertTrue(history.is_file())
            self.assertIn('"status": "uninstalled"', history.read_text())

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

    def test_fresh_install_rejects_a_nonempty_data_directory_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            marker = data / "belongs-to-another-app"
            marker.write_text("preserve", encoding="utf-8")
            config = InstallConfig.from_mapping(values(root), self.identity)
            plan = DeploymentPlan.from_config(config)
            docker = FakeDocker()

            with self.assertRaisesRegex(InstallError, "empty for a fresh install"):
                UnraidInstaller(
                    docker, prepare_data=lambda path: self.fail("must not prepare data")
                ).install(config, plan, root)

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertNotIn("build_image", [call[0] for call in docker.calls])


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
            for resource in (
                "api",
                "proxy",
                "private_network",
                "api_template",
                "proxy_template",
            ):
                self.assertEqual(state.resources[resource]["ownership"], "owned")
            self.assertFalse(
                {"build_image", "create_network", "create_container", "start_container"}
                & {call[0] for call in docker.calls[before:]}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
