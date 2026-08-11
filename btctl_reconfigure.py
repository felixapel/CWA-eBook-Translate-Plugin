"""Provider-only, journaled API-role reconfiguration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

from btctl_compose import (
    InstallError,
    _container_environment,
    _container_networks,
    _labels,
    _probe_runtime_dependencies,
    _verify_data_bind,
    _verify_runtime_sandbox,
    _compose_environment_text,
    render_compose,
)
from btctl_core import (
    ConfigError,
    DeploymentPlan,
    DeploymentState,
    InstallConfig,
    OperationLock,
    StateStore,
    _fsync_directory,
    parse_env_text,
    read_private_text,
)
from btctl_unraid import (
    ContainerSpec,
    _environment_text,
    _unraid_labels,
    _write_private,
)


PROVIDER_ENVIRONMENT = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_CUSTOM_ENDPOINT",
        "LLM_CUSTOM_API_KEY",
        "LLM_FALLBACK_PROVIDER",
        "LLM_FALLBACK_MODEL",
        "LLM_FALLBACK_API_KEY",
        "LLM_FALLBACK_CUSTOM_ENDPOINT",
        "LLM_FALLBACK_CUSTOM_API_KEY",
        "BT_LOCAL_URL",
    }
)


class ProviderReconfigurer:
    """Replace only the API container while preserving runtime ownership."""

    def __init__(self, docker, *, health_timeout_seconds: int = 90):
        self.docker = docker
        self.health_timeout_seconds = health_timeout_seconds

    @staticmethod
    def _artifact_paths(config: InstallConfig) -> dict[str, Path]:
        state_dir = Path(config.state_dir)
        active_name = "api.env"
        return {
            "active": state_dir / active_name,
            "old": state_dir / f"{active_name}.reconfigure-old",
            "new": state_dir / f"{active_name}.reconfigure-new",
            "journal": state_dir / "reconfigure.json",
            "compose": state_dir / "deployment.compose.json",
        }

    @staticmethod
    def _resource_contract_matches(
        state: DeploymentState, plan: DeploymentPlan
    ) -> bool:
        for name, expected in plan.resources.items():
            current = state.resources.get(name)
            if not isinstance(current, dict):
                return False
            if any(current.get(key) != value for key, value in expected.items()):
                return False
        return True

    def _load_current(
        self, config: InstallConfig, plan: DeploymentPlan
    ) -> tuple[
        DeploymentState,
        dict,
        dict,
        dict[str, str],
        dict[str, str],
        dict[str, Path],
    ]:
        self.docker.require_available()
        paths = self._artifact_paths(config)
        if paths["journal"].exists() or paths["journal"].is_symlink():
            raise InstallError(
                "unfinished provider reconfiguration exists; recovery is required"
            )
        state = StateStore(Path(config.state_dir)).load()
        if state.status not in {"installed", "adopted"}:
            raise ConfigError("provider-only reconfigure requires an installed runtime")
        if config.install_profile == "compose-existing" and state.schema_version < 3:
            raise ConfigError(
                "provider-only reconfigure requires uninstalling and reinstalling "
                "the legacy Compose deployment first"
            )
        immutable = (
            state.version == plan.version
            and state.revision == plan.revision
            and state.image == plan.image
            and state.install_profile == plan.install_profile
            and state.auth_profile == plan.auth_profile
            and state.reader_type == plan.reader_type
            and state.reader_contract_version == plan.reader_contract_version
            and self._resource_contract_matches(state, plan)
        )
        if not immutable:
            raise ConfigError(
                "reconfigure is provider-only; runtime, reader, image, and topology "
                "settings must remain unchanged"
            )

        api_name = str(plan.resources["api"]["name"])
        proxy_name = str(plan.resources["proxy"]["name"])
        api = self.docker.inspect_container(api_name)
        proxy = self.docker.inspect_container(proxy_name)
        if not api or not proxy:
            raise InstallError("managed API or proxy container is missing")
        if (
            api.get("Id") != state.resources["api"].get("id")
            or proxy.get("Id") != state.resources["proxy"].get("id")
            or proxy.get("State", {}).get("Status") != "running"
        ):
            raise InstallError("live container identity does not match deployment state")
        if config.install_profile != "unraid":
            try:
                document = json.loads(
                    read_private_text(
                        Path(config.state_dir),
                        paths["compose"].name,
                        label="active Compose document",
                    )
                )
            except (ConfigError, json.JSONDecodeError) as exc:
                raise InstallError("active Compose document is not trustworthy") from exc
            expected_document = render_compose(
                config, plan, state.install_id
            )
            if document != expected_document:
                raise InstallError("active Compose document does not match the plan")
        try:
            current_environment = parse_env_text(
                read_private_text(
                    Path(config.state_dir),
                    paths["active"].name,
                    label="active API environment",
                )
            )
        except ConfigError as exc:
            raise InstallError("active API environment is not trustworthy") from exc
        self._verify_api(config, plan, state, api, current_environment)

        desired = config.api_environment()
        for key, value in desired.items():
            if key not in PROVIDER_ENVIRONMENT and current_environment.get(key) != value:
                raise ConfigError(
                    "reconfigure is provider-only; non-provider API settings changed"
                )
        target_environment = dict(current_environment)
        for key in PROVIDER_ENVIRONMENT:
            target_environment[key] = desired.get(key, "")
        if target_environment == current_environment:
            raise ConfigError("provider configuration is already current")
        return (
            state,
            api,
            proxy,
            current_environment,
            target_environment,
            paths,
        )

    @staticmethod
    def _summary(environment: dict[str, str]) -> dict[str, object]:
        return {
            "provider": environment.get("LLM_PROVIDER", ""),
            "model": environment.get("LLM_MODEL", ""),
            "fallback_provider": environment.get("LLM_FALLBACK_PROVIDER", "") or None,
            "fallback_model": environment.get("LLM_FALLBACK_MODEL", "") or None,
            "uses_local_endpoint": bool(environment.get("BT_LOCAL_URL")),
        }

    def preview(
        self, config: InstallConfig, plan: DeploymentPlan
    ) -> dict[str, object]:
        state, api, proxy, current, target, _paths = self._load_current(
            config, plan
        )
        return {
            "status": "planned",
            "role": "api",
            "install_id": state.install_id,
            "api_container_id": api["Id"],
            "proxy_container_id": proxy["Id"],
            "from": self._summary(current),
            "to": self._summary(target),
            "credentials_changed": any(
                current.get(key, "") != target.get(key, "")
                for key in PROVIDER_ENVIRONMENT
                if key.endswith("API_KEY")
            ),
            "custom_endpoint_changed": any(
                current.get(key, "") != target.get(key, "")
                for key in PROVIDER_ENVIRONMENT
                if key.endswith("CUSTOM_ENDPOINT")
            ),
        }

    def _verify_api(
        self,
        config: InstallConfig,
        plan: DeploymentPlan,
        state: DeploymentState,
        container: dict,
        environment: dict[str, str],
    ) -> None:
        expected_labels = _labels(config, "api", state.install_id)
        labels = container.get("Config", {}).get("Labels", {}) or {}
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise InstallError("API ownership labels do not match deployment state")
        image = self.docker.inspect_image(config.image)
        if not image or container.get("Image") != image.get("Id"):
            raise InstallError("API image identity does not match deployment state")
        live_environment = _container_environment(container)
        if any(live_environment.get(key) != value for key, value in environment.items()):
            raise InstallError("API runtime environment does not match private state")
        _verify_runtime_sandbox(container, "api")
        _verify_data_bind(container, "api", config.data_dir)
        private_name = str(plan.resources["private_network"]["name"])
        external_name = (
            config.reader_network if config.uses_reader_session else config.edge_network
        )
        if _container_networks(container) != {private_name, external_name}:
            raise InstallError("API network topology does not match deployment state")
        private = container.get("NetworkSettings", {}).get("Networks", {}).get(
            private_name, {}
        )
        if "translator-api" not in (private.get("Aliases", []) or []):
            raise InstallError("API private-network alias does not match")

    def _remove_owned_api(
        self, config: InstallConfig, state: DeploymentState, name: str
    ) -> None:
        current = self.docker.inspect_container(name)
        if current is None:
            return
        labels = current.get("Config", {}).get("Labels", {}) or {}
        expected = _labels(config, "api", state.install_id)
        if any(labels.get(key) != value for key, value in expected.items()):
            raise InstallError("refusing to remove an API container with foreign ownership")
        if current.get("State", {}).get("Status") == "running":
            self.docker.stop_container(name)
        self.docker.remove_container(name)

    def _start_api(
        self,
        config: InstallConfig,
        plan: DeploymentPlan,
        state: DeploymentState,
        environment_path: Path,
    ) -> dict:
        api_name = str(plan.resources["api"]["name"])
        if config.install_profile == "unraid":
            private_name = str(plan.resources["private_network"]["name"])
            self.docker.create_container(
                ContainerSpec(
                    role="api",
                    name=api_name,
                    image=config.image,
                    env_file=environment_path,
                    labels=_unraid_labels(config, "api", state.install_id),
                    primary_network=private_name,
                    network_alias="translator-api",
                    data_dir=Path(config.data_dir),
                    publish_port=None,
                )
            )
            external_name = (
                config.reader_network
                if config.uses_reader_session
                else config.edge_network
            )
            self.docker.connect_network(external_name, api_name)
            self.docker.start_container(api_name)
        else:
            document_path = Path(config.state_dir) / "deployment.compose.json"
            self.docker.compose_validate(document_path, config.install_name)
            self.docker.compose_recreate_service(
                document_path, config.install_name, "api"
            )
        self.docker.wait_healthy([api_name], self.health_timeout_seconds)
        self.docker.probe_providers(api_name)
        _probe_runtime_dependencies(self.docker, config, plan)
        container = self.docker.inspect_container(api_name)
        if container is None:
            raise InstallError("API container disappeared after reconfiguration")
        return container

    @staticmethod
    def _save_journal(path: Path, payload: dict[str, object]) -> None:
        _write_private(
            path,
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
        )

    @staticmethod
    def _load_journal(config: InstallConfig, path: Path) -> dict[str, object]:
        try:
            payload = json.loads(
                read_private_text(
                    Path(config.state_dir), path.name,
                    label="provider reconfiguration journal",
                )
            )
        except (ConfigError, json.JSONDecodeError) as exc:
            raise InstallError(
                "provider reconfiguration journal is not trustworthy"
            ) from exc
        expected = {
            "schema_version",
            "status",
            "install_id",
            "old_config_fingerprint",
            "new_config_fingerprint",
            "old_snapshot_sha256",
            "new_snapshot_sha256",
            "role",
            "credentials_changed",
            "custom_endpoint_changed",
            "old_api_id",
            "new_api_id",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload.get("schema_version") != 1
            or payload.get("role") != "api"
            or not isinstance(payload.get("old_api_id"), str)
            or not payload.get("old_api_id")
            or (
                payload.get("new_api_id") is not None
                and (
                    not isinstance(payload.get("new_api_id"), str)
                    or not payload.get("new_api_id")
                )
            )
            or payload.get("status") not in {
                "prepared", "replacing", "verified", "rolling-back",
                "rollback-failed",
            }
        ):
            raise InstallError(
                "provider reconfiguration journal has an unsupported contract"
            )
        return payload

    def _recover_if_needed(
        self, config: InstallConfig, plan: DeploymentPlan
    ) -> tuple[DeploymentState, bool] | None:
        paths = self._artifact_paths(config)
        if not paths["journal"].exists() and not paths["journal"].is_symlink():
            return None
        journal = self._load_journal(config, paths["journal"])
        state = StateStore(Path(config.state_dir)).load()
        if (
            journal["install_id"] != state.install_id
            or journal["new_config_fingerprint"] != plan.config_fingerprint
            or (
                journal["old_config_fingerprint"] != state.config_fingerprint
                and journal["new_config_fingerprint"] != state.config_fingerprint
            )
            or not self._resource_contract_matches(state, plan)
        ):
            raise InstallError(
                "provider reconfiguration recovery evidence does not match runtime state"
            )
        if config.install_profile != "unraid":
            try:
                document = json.loads(read_private_text(
                    Path(config.state_dir), paths["compose"].name,
                    label="active Compose document",
                ))
            except (ConfigError, json.JSONDecodeError) as exc:
                raise InstallError(
                    "active Compose document is not trustworthy"
                ) from exc
            if document != render_compose(config, plan, state.install_id):
                raise InstallError("active Compose document does not match the plan")
        snapshots: dict[str, str] = {}
        for key in ("old", "new"):
            try:
                snapshots[key] = read_private_text(
                    Path(config.state_dir), paths[key].name,
                    label=f"{key} provider environment snapshot",
                )
            except ConfigError as exc:
                raise InstallError(
                    "provider reconfiguration snapshot is not trustworthy"
                ) from exc
            digest = hashlib.sha256(snapshots[key].encode("utf-8")).hexdigest()
            if digest != journal[f"{key}_snapshot_sha256"]:
                raise InstallError(
                    "provider reconfiguration snapshot digest does not match"
                )

        api_name = str(plan.resources["api"]["name"])
        container = self.docker.inspect_container(api_name)
        committed = (
            journal["status"] == "verified"
            and journal["new_api_id"] is not None
            and state.resources["api"].get("id") == journal["new_api_id"]
            and state.config_fingerprint == plan.config_fingerprint
        )
        if committed:
            if container is None or container.get("Id") != journal["new_api_id"]:
                raise InstallError(
                    "committed provider reconfiguration API identity is missing"
                )
            live = _container_environment(container)
            desired = config.api_environment()
            if any(live.get(key) != value for key, value in desired.items()):
                raise InstallError(
                    "committed provider reconfiguration runtime does not match state"
                )
            self._verify_api(config, plan, state, container, live)
            self.docker.probe_providers(api_name)
            _probe_runtime_dependencies(self.docker, config, plan)
            self._cleanup(paths)
            return state, True

        if (
            state.config_fingerprint != journal["old_config_fingerprint"]
            or state.resources["api"].get("id") != journal["old_api_id"]
        ):
            raise InstallError(
                "provider reconfiguration recovery found unknown state"
            )
        journal["status"] = "rolling-back"
        self._save_journal(paths["journal"], journal)
        if config.install_profile == "unraid":
            self._remove_owned_api(config, state, api_name)
        _write_private(paths["active"], snapshots["old"])
        restored = self._start_api(config, plan, state, paths["active"])
        old_environment = parse_env_text(snapshots["old"])
        self._verify_api(config, plan, state, restored, old_environment)
        resources = copy.deepcopy(state.resources)
        resources["api"]["id"] = restored["Id"]
        restored_state = replace(state, resources=resources)
        StateStore(Path(config.state_dir)).save(restored_state)
        self._cleanup(paths)
        return restored_state, False

    @staticmethod
    def _cleanup(paths: dict[str, Path]) -> None:
        for key in ("old", "new", "journal"):
            try:
                paths[key].unlink()
            except FileNotFoundError:
                pass
        _fsync_directory(paths["active"].parent)

    def reconfigure(
        self,
        config: InstallConfig,
        plan: DeploymentPlan,
        *,
        _operation_locked: bool = False,
    ) -> DeploymentState:
        if not _operation_locked:
            with OperationLock(Path(config.state_dir)):
                return self.reconfigure(
                    config, plan, _operation_locked=True
                )
        recovery = self._recover_if_needed(config, plan)
        if recovery is not None:
            recovered, committed = recovery
            if committed:
                return recovered
        (
            state,
            api,
            proxy,
            current_environment,
            target_environment,
            paths,
        ) = self._load_current(config, plan)
        old_text = read_private_text(
            Path(config.state_dir), paths["active"].name,
            label="active API environment",
        )
        new_text = (
            _environment_text(target_environment)
            if config.install_profile == "unraid"
            else _compose_environment_text(target_environment)
        )
        credentials_changed = any(
            current_environment.get(key, "") != target_environment.get(key, "")
            for key in PROVIDER_ENVIRONMENT
            if key.endswith("API_KEY")
        )
        custom_endpoint_changed = any(
            current_environment.get(key, "") != target_environment.get(key, "")
            for key in PROVIDER_ENVIRONMENT
            if key.endswith("CUSTOM_ENDPOINT")
        )
        _write_private(paths["old"], old_text)
        _write_private(paths["new"], new_text)
        journal = {
            "schema_version": 1,
            "status": "prepared",
            "install_id": state.install_id,
            "old_config_fingerprint": state.config_fingerprint,
            "new_config_fingerprint": plan.config_fingerprint,
            "old_snapshot_sha256": hashlib.sha256(
                old_text.encode("utf-8")
            ).hexdigest(),
            "new_snapshot_sha256": hashlib.sha256(
                new_text.encode("utf-8")
            ).hexdigest(),
            "role": "api",
            "credentials_changed": credentials_changed,
            "custom_endpoint_changed": custom_endpoint_changed,
            "old_api_id": api["Id"],
            "new_api_id": None,
        }
        self._save_journal(paths["journal"], journal)
        api_name = str(plan.resources["api"]["name"])
        committed = False
        try:
            journal["status"] = "replacing"
            self._save_journal(paths["journal"], journal)
            if config.install_profile == "unraid":
                self._remove_owned_api(config, state, api_name)
            _write_private(paths["active"], new_text)
            replacement = self._start_api(
                config, plan, state, paths["active"]
            )
            self._verify_api(
                config, plan, state, replacement, target_environment
            )
            if self.docker.inspect_container(str(plan.resources["proxy"]["name"]))[
                "Id"
            ] != proxy["Id"]:
                raise InstallError("proxy identity changed during API reconfiguration")
            journal["new_api_id"] = replacement["Id"]
            resources = copy.deepcopy(state.resources)
            resources["api"]["id"] = replacement["Id"]
            updated = replace(
                state,
                config_fingerprint=plan.config_fingerprint,
                resources=resources,
            )
            journal["status"] = "verified"
            self._save_journal(paths["journal"], journal)
            StateStore(Path(config.state_dir)).save(updated)
            committed = True
            self._cleanup(paths)
            return updated
        except BaseException as exc:
            if committed:
                raise InstallError(
                    "provider reconfiguration committed but journal cleanup failed"
                ) from exc
            try:
                journal["status"] = "rolling-back"
                self._save_journal(paths["journal"], journal)
                if config.install_profile == "unraid":
                    self._remove_owned_api(config, state, api_name)
                _write_private(paths["active"], old_text)
                restored = self._start_api(
                    config, plan, state, paths["active"]
                )
                self._verify_api(
                    config, plan, state, restored, current_environment
                )
                resources = copy.deepcopy(state.resources)
                resources["api"]["id"] = restored["Id"]
                StateStore(Path(config.state_dir)).save(
                    replace(state, resources=resources)
                )
                self._cleanup(paths)
            except BaseException as rollback_exc:
                journal["status"] = "rollback-failed"
                self._save_journal(paths["journal"], journal)
                raise InstallError(
                    "provider reconfiguration and automatic rollback both failed; "
                    "private recovery evidence was retained"
                ) from rollback_exc
            raise InstallError(
                "provider reconfiguration failed and was rolled back"
            ) from exc
