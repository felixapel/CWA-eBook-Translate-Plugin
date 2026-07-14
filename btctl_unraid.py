"""Unraid adapter using local Docker only; no SSH, registry, or CWA overlay."""

from __future__ import annotations

import copy
import hashlib
import os
import string
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol
from xml.sax.saxutils import escape

from btctl_compose import (
    ComposeInstaller,
    InstallError,
    _container_networks,
    _has_exact_cwa_version,
    _labels,
    _verify_identity_edge_artifact,
)
from btctl_auth import render_authentik_edge
from btctl_core import DeploymentPlan, DeploymentState, InstallConfig, StateStore


TEMPLATE_ROOT = Path(__file__).parent / "deploy" / "unraid"


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    role: str
    name: str
    image: str
    env_file: Path
    labels: dict[str, str]
    primary_network: str
    network_alias: str
    data_dir: Path | None
    publish_port: int | None


class UnraidDocker(Protocol):
    def require_available(self) -> None: ...
    def inspect_container(self, name: str) -> dict | None: ...
    def inspect_network(self, name: str) -> dict | None: ...
    def inspect_image(self, name: str) -> dict | None: ...
    def build_image(self, repository: Path, image: str, labels: dict[str, str]) -> None: ...
    def create_network(self, name: str, labels: dict[str, str], *, internal: bool) -> None: ...
    def create_container(self, spec: ContainerSpec) -> None: ...
    def connect_network(self, network: str, container: str) -> None: ...
    def start_container(self, name: str) -> None: ...
    def wait_healthy(self, names: list[str], timeout_seconds: int) -> None: ...
    def remove_container(self, name: str) -> None: ...
    def remove_network(self, name: str) -> None: ...


def _xml(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def render_templates(config: InstallConfig, plan: DeploymentPlan) -> dict[str, str]:
    """Render informational DockerMan templates without embedding secrets."""
    api_source = (TEMPLATE_ROOT / "my-cwa-translate-api.xml.tmpl").read_text(
        encoding="utf-8"
    )
    proxy_source = (TEMPLATE_ROOT / "my-cwa-translate-proxy.xml.tmpl").read_text(
        encoding="utf-8"
    )
    state_dir = Path(config.state_dir)
    common = {
        "IMAGE": _xml(config.image),
        "PRIVATE_NETWORK": _xml(plan.resources["private_network"]["name"]),
    }
    api = string.Template(api_source).substitute(
        **common,
        NAME=_xml(plan.resources["api"]["name"]),
        ENV_FILE=_xml(state_dir / "api.env"),
        DATA_DIR=_xml(config.data_dir),
    )
    if config.proxy_port is None:
        port_config = ""
    else:
        port = _xml(config.proxy_port)
        port_config = (
            f'  <Config Name="Web port" Target="8080" Default="{port}" '
            f'Mode="tcp" Description="Only browser-facing translator port." '
            f'Type="Port" Display="always" Required="true" Mask="false">'
            f"{port}</Config>\n"
        )
    proxy = string.Template(proxy_source).substitute(
        **common,
        NAME=_xml(plan.resources["proxy"]["name"]),
        ENV_FILE=_xml(state_dir / "proxy.env"),
        PUBLIC_ORIGIN=_xml(config.public_origin),
        PORT_CONFIG=port_config,
    )
    try:
        ET.fromstring(api)
        ET.fromstring(proxy)
    except ET.ParseError as exc:
        raise InstallError("generated Unraid template is not valid XML") from exc
    return {"api": api, "proxy": proxy}


def _write_private(path: Path, text: str, *, private_parent: bool = True) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise InstallError("managed file destination must not be a symbolic link")
    path.parent.mkdir(
        parents=True, mode=0o700 if private_parent else 0o755, exist_ok=True
    )
    if private_parent:
        os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
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


def _environment_text(values: dict[str, str]) -> str:
    lines = []
    for name, value in sorted(values.items()):
        if "\n" in value or "\r" in value or "\0" in value:
            raise InstallError("runtime environment contains an unsafe value")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def prepare_data_directory(path: Path) -> None:
    if path.is_symlink():
        raise InstallError("BT_DATA_DIR must not be a symbolic link")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    if os.geteuid() == 0:
        os.chown(path, 101, 102)
    else:
        metadata = path.stat()
        if (metadata.st_uid, metadata.st_gid) != (101, 102):
            raise InstallError(
                "BT_DATA_DIR must be owned by uid 101 gid 102; run btctl as root on Unraid"
            )


def _unraid_labels(config: InstallConfig, role: str, install_id: str) -> dict[str, str]:
    labels = _labels(config, role, install_id)
    labels.update(
        {
            "net.unraid.docker.managed": "dockerman",
            "net.unraid.docker.webui": config.public_origin,
        }
    )
    return labels


class UnraidInstaller:
    def __init__(
        self,
        docker: UnraidDocker,
        *,
        health_timeout_seconds: int = 90,
        prepare_data: Callable[[Path], None] = prepare_data_directory,
    ):
        self.docker = docker
        self.health_timeout_seconds = health_timeout_seconds
        self.prepare_data = prepare_data

    def _preflight(self, config: InstallConfig, plan: DeploymentPlan) -> None:
        if config.install_profile != "unraid":
            raise InstallError("Unraid installer requires the unraid profile")
        self.docker.require_available()
        if StateStore(Path(config.state_dir)).path.exists():
            raise InstallError("deployment state already exists; use doctor or upgrade")
        cwa = self.docker.inspect_container(config.cwa_container)
        if cwa is None or cwa.get("State", {}).get("Status") != "running":
            raise InstallError("configured CWA container is missing or stopped")
        if not _has_exact_cwa_version(cwa, config.cwa_version):
            raise InstallError("configured CWA version lacks exact runtime evidence")
        if config.cwa_network not in _container_networks(cwa):
            raise InstallError("configured CWA is not on BT_CWA_NETWORK")
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
        for role in ("api", "proxy"):
            target = Path(plan.resources[f"{role}_template"]["path"])
            if target.exists() or target.is_symlink():
                raise InstallError(f"Unraid {role} template already exists")

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
        verifier = ComposeInstaller(self.docker)
        image_id = verifier._verify_image(config, self.docker.inspect_image(config.image))
        self.prepare_data(Path(config.data_dir))

        state_dir = Path(config.state_dir)
        api_env = state_dir / "api.env"
        proxy_env = state_dir / "proxy.env"
        _write_private(api_env, _environment_text({**config.api_environment(), "BT_ROLE": "api"}))
        _write_private(
            proxy_env,
            _environment_text(
                {
                    **config.proxy_environment(),
                    "BT_ROLE": "proxy",
                    "BT_API_UPSTREAM": f"http://{plan.resources['api']['name']}:8390",
                }
            ),
        )
        templates = render_templates(config, plan)
        for role, source in templates.items():
            _write_private(state_dir / f"{role}.template.xml", source)
        if config.auth_profile == "authentik-forwarded":
            artifact = render_authentik_edge(config, plan)
            artifact_path = Path(str(plan.resources["identity_edge_config"]["path"]))
            if artifact_path.name != artifact.filename:
                raise InstallError("identity-edge artifact name does not match the plan")
            _write_private(artifact_path, artifact.content)

        install_id = str(uuid.uuid4())
        private_name = str(plan.resources["private_network"]["name"])
        created_network = False
        created_roles: list[str] = []
        copied_templates: list[Path] = []
        try:
            self.docker.create_network(
                private_name,
                _labels(config, "private-network", install_id),
                internal=True,
            )
            created_network = True
            api_name = str(plan.resources["api"]["name"])
            self.docker.create_container(
                ContainerSpec(
                    role="api",
                    name=api_name,
                    image=config.image,
                    env_file=api_env,
                    labels=_unraid_labels(config, "api", install_id),
                    primary_network=private_name,
                    network_alias="translator-api",
                    data_dir=Path(config.data_dir),
                    publish_port=None,
                )
            )
            created_roles.append(api_name)
            api_external = (
                config.cwa_network
                if config.auth_profile == "cwa-session"
                else config.edge_network
            )
            self.docker.connect_network(api_external, api_name)
            self.docker.start_container(api_name)
            self.docker.wait_healthy([api_name], self.health_timeout_seconds)

            proxy_name = str(plan.resources["proxy"]["name"])
            self.docker.create_container(
                ContainerSpec(
                    role="proxy",
                    name=proxy_name,
                    image=config.image,
                    env_file=proxy_env,
                    labels=_unraid_labels(config, "proxy", install_id),
                    primary_network=private_name,
                    network_alias="",
                    data_dir=None,
                    publish_port=config.proxy_port,
                )
            )
            created_roles.append(proxy_name)
            self.docker.connect_network(config.cwa_network, proxy_name)
            if config.edge_network:
                self.docker.connect_network(config.edge_network, proxy_name)
            self.docker.start_container(proxy_name)
            self.docker.wait_healthy([proxy_name], self.health_timeout_seconds)

            resources = copy.deepcopy(plan.resources)
            for role in ("api", "proxy"):
                container_id, _ = verifier._verify_container(
                    config, plan, install_id, role, image_id
                )
                resources[role]["id"] = container_id
            private = self.docker.inspect_network(private_name)
            if private is None or not isinstance(private.get("Id"), str):
                raise InstallError("private network is missing after startup")
            resources["private_network"]["id"] = private["Id"]
            _verify_identity_edge_artifact(config, plan, resources)

            for role, source in templates.items():
                target = Path(plan.resources[f"{role}_template"]["path"])
                _write_private(target, source, private_parent=False)
                copied_templates.append(target)
                resources[f"{role}_template"]["sha256"] = hashlib.sha256(
                    source.encode("utf-8")
                ).hexdigest()
            state = replace(
                DeploymentState.new(install_id=install_id, plan=plan),
                resources=resources,
            )
            StateStore(state_dir).save(state)
            return state
        except BaseException:
            for target in reversed(copied_templates):
                try:
                    target.unlink()
                except OSError:
                    pass
            for name in reversed(created_roles):
                try:
                    self.docker.remove_container(name)
                except BaseException:
                    pass
            if created_network:
                try:
                    self.docker.remove_network(private_name)
                except BaseException:
                    pass
            raise


class UnraidAdopter:
    """Recover Unraid state only from complete matching btctl evidence."""

    def __init__(self, docker: UnraidDocker):
        self.docker = docker

    def adopt(self, config: InstallConfig, plan: DeploymentPlan) -> DeploymentState:
        if config.install_profile != "unraid":
            raise InstallError("Unraid adoption requires the unraid profile")
        self.docker.require_available()
        store = StateStore(Path(config.state_dir))
        if store.path.exists():
            raise InstallError("deployment state already exists; use doctor")
        cwa = self.docker.inspect_container(config.cwa_container)
        if (
            cwa is None
            or cwa.get("State", {}).get("Status") != "running"
            or not _has_exact_cwa_version(cwa, config.cwa_version)
            or config.cwa_network not in _container_networks(cwa)
        ):
            raise InstallError("configured CWA evidence does not match")
        containers = {
            role: self.docker.inspect_container(str(plan.resources[role]["name"]))
            for role in ("api", "proxy")
        }
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
        if containers["proxy"]["Config"]["Labels"].get(
            "io.cwa-translate.install-id"
        ) != install_id:
            raise InstallError("split runtime install-id labels do not match")
        verifier = ComposeInstaller(self.docker)
        image_id = verifier._verify_image(config, self.docker.inspect_image(config.image))
        resources = copy.deepcopy(plan.resources)
        for role in ("api", "proxy"):
            if containers[role].get("State", {}).get("Health", {}).get("Status") != "healthy":
                raise InstallError(f"{role} container is not healthy")
            container_id, _ = verifier._verify_container(
                config, plan, install_id, role, image_id
            )
            resources[role]["id"] = container_id
            resources[role]["ownership"] = "adopted"
        private = self.docker.inspect_network(
            str(plan.resources["private_network"]["name"])
        )
        labels = private.get("Labels", {}) if private else {}
        if (
            not private
            or not isinstance(private.get("Id"), str)
            or labels.get("io.cwa-translate.install-id") != install_id
            or labels.get("io.cwa-translate.role") != "private-network"
        ):
            raise InstallError("private network ownership evidence does not match")
        resources["private_network"]["id"] = private["Id"]
        resources["private_network"]["ownership"] = "adopted"
        _verify_identity_edge_artifact(config, plan, resources)
        if config.auth_profile == "authentik-forwarded":
            resources["identity_edge_config"]["ownership"] = "adopted"
        for role in ("api", "proxy"):
            path = Path(plan.resources[f"{role}_template"]["path"])
            if not path.is_file() or path.is_symlink():
                raise InstallError(f"{role} Unraid template is missing")
            expected = render_templates(config, plan)[role].encode("utf-8")
            if path.read_bytes() != expected:
                raise InstallError(f"{role} Unraid template does not match the plan")
            resources[f"{role}_template"]["ownership"] = "adopted"
            resources[f"{role}_template"]["sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        state = replace(
            DeploymentState.new(install_id=install_id, plan=plan),
            status="adopted",
            resources=resources,
        )
        store.save(state)
        return state
