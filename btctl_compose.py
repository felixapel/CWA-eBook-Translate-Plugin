"""Deterministic Compose model and safe installation orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from btctl_auth import render_authentik_edge
from btctl_core import DeploymentPlan, DeploymentState, InstallConfig, StateStore


class InstallError(RuntimeError):
    """A live deployment precondition or postcondition was not satisfied."""


class ComposeDocker(Protocol):
    def require_available(self) -> None: ...
    def inspect_container(self, name: str) -> dict | None: ...
    def inspect_network(self, name: str) -> dict | None: ...
    def inspect_image(self, name: str) -> dict | None: ...
    def build_image(self, repository: Path, image: str, labels: dict[str, str]) -> None: ...
    def prepare_data_directory(self, image: str, path: Path) -> None: ...
    def compose_validate(self, document: Path, project: str) -> None: ...
    def compose_up(self, document: Path, project: str) -> None: ...
    def wait_healthy(self, names: list[str], timeout_seconds: int) -> None: ...
    def compose_down(self, document: Path, project: str) -> None: ...


def _labels(config: InstallConfig, role: str, install_id: str) -> dict[str, str]:
    return {
        "io.cwa-translate.managed-by": "btctl",
        "io.cwa-translate.install-id": install_id,
        "io.cwa-translate.role": role,
        "io.cwa-translate.version": config.identity.version,
        "io.cwa-translate.revision": config.identity.sha,
    }


def _service_security() -> dict[str, object]:
    return {
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "restart": "unless-stopped",
    }


def render_compose(
    config: InstallConfig, plan: DeploymentPlan, install_id: str
) -> dict[str, object]:
    """Return a JSON-compatible Compose model with no implicit CWA ownership."""
    api_environment = {
        **config.api_environment(),
        "BT_ROLE": "api",
    }
    proxy_environment = {
        **config.proxy_environment(),
        "BT_ROLE": "proxy",
        "BT_API_UPSTREAM": f"http://{plan.resources['api']['name']}:8390",
    }
    api_networks: dict[str, object] = {"private": {"aliases": ["translator-api"]}}
    if config.auth_profile == "cwa-session":
        api_networks["cwa"] = {}
    else:
        api_networks["edge"] = {}
    proxy_networks: dict[str, object] = {"private": {}, "cwa": {}}
    if config.edge_network:
        proxy_networks["edge"] = {}

    api = {
        "image": config.image,
        "pull_policy": "never",
        "container_name": plan.resources["api"]["name"],
        "environment": api_environment,
        "labels": _labels(config, "api", install_id),
        "volumes": [
            {
                "type": "bind",
                "source": config.data_dir,
                "target": "/app/data",
            }
        ],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=67108864,uid=101,gid=102,mode=700"],
        "pids_limit": 256,
        "mem_limit": "1g",
        "cpus": 2.0,
        "extra_hosts": ["host.docker.internal:host-gateway"],
        "networks": api_networks,
        **_service_security(),
    }
    proxy = {
        "image": config.image,
        "pull_policy": "never",
        "container_name": plan.resources["proxy"]["name"],
        "environment": proxy_environment,
        "labels": _labels(config, "proxy", install_id),
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=67108864,uid=101,gid=102,mode=700"],
        "pids_limit": 64,
        "mem_limit": "128m",
        "cpus": 0.5,
        "depends_on": {"api": {"condition": "service_healthy"}},
        "networks": proxy_networks,
        **_service_security(),
    }
    if config.proxy_port is not None:
        proxy["ports"] = [
            {"target": 8080, "published": config.proxy_port, "protocol": "tcp"}
        ]

    networks: dict[str, object] = {
        "private": {
            "name": plan.resources["private_network"]["name"],
            "internal": True,
            "labels": _labels(config, "private-network", install_id),
        },
        "cwa": {"name": config.cwa_network, "external": True},
    }
    if config.edge_network:
        networks["edge"] = {"name": config.edge_network, "external": True}
    return {
        "name": config.install_name,
        "services": {"api": api, "proxy": proxy},
        "networks": networks,
    }


def _write_private_json(path: Path, payload: object) -> None:
    directory = path.parent
    if directory.is_symlink() or path.is_symlink():
        raise InstallError("deployment artifact path must not be a symbolic link")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_private_text(path: Path, content: str) -> None:
    directory = path.parent
    if directory.is_symlink() or path.is_symlink():
        raise InstallError("deployment artifact path must not be a symbolic link")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _verify_identity_edge_artifact(
    config: InstallConfig,
    plan: DeploymentPlan,
    resources: dict[str, dict[str, object]],
) -> None:
    if config.auth_profile != "authentik-forwarded":
        return
    artifact = render_authentik_edge(config, plan)
    path = Path(str(plan.resources["identity_edge_config"]["path"]))
    if (
        path.is_symlink()
        or not path.is_file()
        or path.read_text(encoding="utf-8") != artifact.content
    ):
        raise InstallError("identity-edge configuration does not match the plan")
    resources["identity_edge_config"]["sha256"] = hashlib.sha256(
        artifact.content.encode("utf-8")
    ).hexdigest()


def _container_networks(container: dict) -> set[str]:
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    return set(networks) if isinstance(networks, dict) else set()


def _container_environment(container: dict) -> dict[str, str]:
    environment = container.get("Config", {}).get("Env", [])
    if not isinstance(environment, list):
        return {}
    parsed: dict[str, str] = {}
    for item in environment:
        if isinstance(item, str) and "=" in item:
            name, value = item.split("=", 1)
            parsed[name] = value
    return parsed


def _has_exact_cwa_version(container: dict, version: str) -> bool:
    image = container.get("Config", {}).get("Image", "")
    without_digest = image.split("@", 1)[0] if isinstance(image, str) else ""
    last_component = without_digest.rsplit("/", 1)[-1]
    tag = last_component.rsplit(":", 1)[1] if ":" in last_component else ""
    if tag in {version, f"v{version}"}:
        return True
    labels = container.get("Config", {}).get("Labels", {}) or {}
    return any(
        labels.get(name) in {version, f"v{version}"}
        for name in (
            "org.opencontainers.image.version",
            "org.label-schema.version",
            "version",
        )
    )


class ComposeInstaller:
    def __init__(self, docker: ComposeDocker, *, health_timeout_seconds: int = 90):
        self.docker = docker
        self.health_timeout_seconds = health_timeout_seconds

    def _preflight(self, config: InstallConfig, plan: DeploymentPlan) -> None:
        if config.install_profile != "compose-existing":
            raise InstallError("Compose installer requires compose-existing profile")
        self.docker.require_available()
        if StateStore(Path(config.state_dir)).path.exists():
            raise InstallError("deployment state already exists; use doctor or upgrade")
        cwa = self.docker.inspect_container(config.cwa_container)
        if cwa is None:
            raise InstallError("configured CWA container does not exist")
        if cwa.get("State", {}).get("Status") != "running":
            raise InstallError("configured CWA container is not running")
        if not _has_exact_cwa_version(cwa, config.cwa_version):
            raise InstallError(
                "configured CWA version has no exact tag or image-label evidence"
            )
        if config.cwa_network not in _container_networks(cwa):
            raise InstallError("configured CWA container is not on BT_CWA_NETWORK")
        if self.docker.inspect_network(config.cwa_network) is None:
            raise InstallError("BT_CWA_NETWORK does not exist")
        if config.edge_network and self.docker.inspect_network(config.edge_network) is None:
            raise InstallError("BT_EDGE_NETWORK does not exist")
        for role in ("api", "proxy"):
            name = str(plan.resources[role]["name"])
            if self.docker.inspect_container(name) is not None:
                raise InstallError(f"container {name} already exists")
        private_name = str(plan.resources["private_network"]["name"])
        if self.docker.inspect_network(private_name) is not None:
            raise InstallError(f"network {private_name} already exists")

    def _verify_image(self, config: InstallConfig, image: dict | None) -> str:
        if image is None or not isinstance(image.get("Id"), str):
            raise InstallError("local image build did not produce an inspectable image")
        labels = image.get("Config", {}).get("Labels", {}) or {}
        expected = {
            "io.cwa-translate.version": config.identity.version,
            "io.cwa-translate.revision": config.identity.sha,
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            raise InstallError("local image identity labels do not match the checkout")
        return image["Id"]

    def _verify_container(
        self,
        config: InstallConfig,
        plan: DeploymentPlan,
        install_id: str,
        role: str,
        image_id: str,
    ) -> tuple[str, dict]:
        name = str(plan.resources[role]["name"])
        container = self.docker.inspect_container(name)
        if container is None or not isinstance(container.get("Id"), str):
            raise InstallError(f"{role} container is missing after startup")
        if container.get("Image") != image_id:
            raise InstallError(f"{role} container image ID does not match the local build")
        labels = container.get("Config", {}).get("Labels", {}) or {}
        expected_labels = _labels(config, role, install_id)
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise InstallError(f"{role} container ownership labels do not match")
        expected_environment = (
            {**config.api_environment(), "BT_ROLE": "api"}
            if role == "api"
            else {
                **config.proxy_environment(),
                "BT_ROLE": "proxy",
                "BT_API_UPSTREAM": f"http://{plan.resources['api']['name']}:8390",
            }
        )
        live_environment = _container_environment(container)
        if any(
            live_environment.get(key) != value
            for key, value in expected_environment.items()
        ) or "BT_ALLOW_INSECURE_AUTH" in live_environment:
            raise InstallError(f"{role} container runtime environment does not match")
        bindings = container.get("HostConfig", {}).get("PortBindings") or {}
        if role == "api" and bindings:
            raise InstallError("API container unexpectedly publishes a host port")
        if role == "proxy":
            expected_binding = config.proxy_port is not None
            if bool(bindings) != expected_binding:
                raise InstallError("proxy host-port binding does not match the plan")
        private_name = str(plan.resources["private_network"]["name"])
        expected_networks = {private_name}
        if role == "api":
            expected_networks.add(
                config.cwa_network if config.auth_profile == "cwa-session" else config.edge_network
            )
        else:
            expected_networks.add(config.cwa_network)
            if config.edge_network:
                expected_networks.add(config.edge_network)
        if _container_networks(container) != expected_networks:
            raise InstallError(f"{role} container networks do not match the plan")
        return container["Id"], container

    def install(
        self, config: InstallConfig, plan: DeploymentPlan, repository: Path
    ) -> DeploymentState:
        self._preflight(config, plan)
        image_labels = {
            "io.cwa-translate.version": config.identity.version,
            "io.cwa-translate.revision": config.identity.sha,
            "io.cwa-translate.source": "local-checkout",
        }
        self.docker.build_image(Path(repository), config.image, image_labels)
        image_id = self._verify_image(config, self.docker.inspect_image(config.image))

        data_dir = Path(config.data_dir)
        if data_dir.is_symlink():
            raise InstallError("BT_DATA_DIR must not be a symbolic link")
        data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(data_dir, 0o700)
        self.docker.prepare_data_directory(config.image, data_dir)

        install_id = str(uuid.uuid4())
        document_path = Path(config.state_dir) / "deployment.compose.json"
        _write_private_json(document_path, render_compose(config, plan, install_id))
        if config.auth_profile == "authentik-forwarded":
            artifact = render_authentik_edge(config, plan)
            artifact_path = Path(str(plan.resources["identity_edge_config"]["path"]))
            if artifact_path.name != artifact.filename:
                raise InstallError("identity-edge artifact name does not match the plan")
            _write_private_text(artifact_path, artifact.content)
        started = False
        try:
            self.docker.compose_validate(document_path, config.install_name)
            self.docker.compose_up(document_path, config.install_name)
            started = True
            names = [str(plan.resources[role]["name"]) for role in ("api", "proxy")]
            self.docker.wait_healthy(names, self.health_timeout_seconds)
            resources = copy.deepcopy(plan.resources)
            for role in ("api", "proxy"):
                container_id, _ = self._verify_container(
                    config, plan, install_id, role, image_id
                )
                resources[role]["id"] = container_id
            private = self.docker.inspect_network(
                str(plan.resources["private_network"]["name"])
            )
            if private is None or not isinstance(private.get("Id"), str):
                raise InstallError("private network is missing after startup")
            resources["private_network"]["id"] = private["Id"]
            _verify_identity_edge_artifact(config, plan, resources)
            state = replace(
                DeploymentState.new(install_id=install_id, plan=plan),
                resources=resources,
            )
            StateStore(Path(config.state_dir)).save(state)
            return state
        except BaseException:
            if started:
                self.docker.compose_down(document_path, config.install_name)
            raise


class ComposeAdopter:
    """Recover state for an already-labeled split runtime without changing Docker."""

    def __init__(self, docker: ComposeDocker):
        self.docker = docker

    def adopt(self, config: InstallConfig, plan: DeploymentPlan) -> DeploymentState:
        if config.install_profile != "compose-existing":
            raise InstallError("Compose adoption requires compose-existing profile")
        self.docker.require_available()
        store = StateStore(Path(config.state_dir))
        if store.path.exists():
            raise InstallError("deployment state already exists; use doctor")
        cwa = self.docker.inspect_container(config.cwa_container)
        if cwa is None or cwa.get("State", {}).get("Status") != "running":
            raise InstallError("configured CWA container is missing or stopped")
        if not _has_exact_cwa_version(cwa, config.cwa_version):
            raise InstallError(
                "configured CWA version has no exact tag or image-label evidence"
            )
        if config.cwa_network not in _container_networks(cwa):
            raise InstallError("configured CWA container is not on BT_CWA_NETWORK")
        if self.docker.inspect_network(config.cwa_network) is None:
            raise InstallError("BT_CWA_NETWORK does not exist")
        if config.edge_network and self.docker.inspect_network(config.edge_network) is None:
            raise InstallError("BT_EDGE_NETWORK does not exist")

        containers = {
            role: self.docker.inspect_container(str(plan.resources[role]["name"]))
            for role in ("api", "proxy")
        }
        if all(container is None for container in containers.values()):
            legacy = self.docker.inspect_container(config.install_name)
            legacy_image = (
                legacy.get("Config", {}).get("Image", "") if legacy else ""
            )
            if legacy and "2.1.4" in legacy_image:
                raise InstallError("combined v2.1.4 runtime requires the upgrade command")
            raise InstallError("the expected split runtime does not exist")
        for role, container in containers.items():
            labels = container.get("Config", {}).get("Labels", {}) if container else {}
            if (
                not container
                or labels.get("io.cwa-translate.managed-by") != "btctl"
                or labels.get("io.cwa-translate.role") != role
                or labels.get("io.cwa-translate.version") != config.identity.version
                or labels.get("io.cwa-translate.revision") != config.identity.sha
                or not labels.get("io.cwa-translate.install-id")
            ):
                raise InstallError(f"{role} ownership labels are missing or incompatible")
        install_id = containers["api"]["Config"]["Labels"][
            "io.cwa-translate.install-id"
        ]
        if (
            containers["proxy"]["Config"]["Labels"].get(
                "io.cwa-translate.install-id"
            )
            != install_id
        ):
            raise InstallError("split runtime install-id labels do not match")
        for role, container in containers.items():
            if container.get("State", {}).get("Health", {}).get("Status") != "healthy":
                raise InstallError(f"{role} container is not healthy")

        verifier = ComposeInstaller(self.docker)
        image_id = verifier._verify_image(
            config, self.docker.inspect_image(config.image)
        )
        resources = copy.deepcopy(plan.resources)
        for role in ("api", "proxy"):
            container_id, _ = verifier._verify_container(
                config, plan, install_id, role, image_id
            )
            resources[role]["id"] = container_id
            resources[role]["ownership"] = "adopted"

        private = self.docker.inspect_network(
            str(plan.resources["private_network"]["name"])
        )
        private_labels = private.get("Labels", {}) if private else {}
        if (
            not private
            or not isinstance(private.get("Id"), str)
            or private_labels.get("io.cwa-translate.install-id") != install_id
            or private_labels.get("io.cwa-translate.role") != "private-network"
            or private_labels.get("io.cwa-translate.revision") != config.identity.sha
        ):
            raise InstallError("private network ownership labels are missing or incompatible")
        resources["private_network"]["id"] = private["Id"]
        resources["private_network"]["ownership"] = "adopted"
        _verify_identity_edge_artifact(config, plan, resources)
        if config.auth_profile == "authentik-forwarded":
            resources["identity_edge_config"]["ownership"] = "adopted"
        state = replace(
            DeploymentState.new(install_id=install_id, plan=plan),
            status="adopted",
            resources=resources,
        )
        store.save(state)
        return state
