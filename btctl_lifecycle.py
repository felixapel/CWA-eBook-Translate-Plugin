"""Fail-closed diagnosis, removal, and v2.1.4 migration orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from btctl_auth import render_authentik_edge
from btctl_compose import (
    ComposeInstaller,
    InstallError,
    _container_networks,
    _has_exact_cwa_version,
    _labels,
    _verify_identity_edge_artifact,
    render_compose,
)
from btctl_core import DeploymentPlan, DeploymentState, InstallConfig, StateStore
from btctl_unraid import UnraidInstaller, _environment_text, render_templates


_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_JOURNAL_SCHEMA = 1


class LifecycleDocker(Protocol):
    def require_available(self) -> None: ...
    def inspect_container(self, name: str) -> dict | None: ...
    def inspect_network(self, name: str) -> dict | None: ...
    def inspect_image(self, name: str) -> dict | None: ...
    def remove_container(self, name: str) -> None: ...
    def remove_network(self, name: str) -> None: ...
    def stop_container(self, name: str) -> None: ...
    def start_container(self, name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DoctorReport:
    ok: bool
    checks: list[dict[str, str]]

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "checks": self.checks}


def _state_matches_plan(
    state: DeploymentState, config: InstallConfig, plan: DeploymentPlan
) -> None:
    expected = {
        "version": plan.version,
        "revision": plan.revision,
        "image": plan.image,
        "config_fingerprint": plan.config_fingerprint,
        "install_profile": plan.install_profile,
        "auth_profile": plan.auth_profile,
    }
    if any(getattr(state, name) != value for name, value in expected.items()):
        raise InstallError("deployment state does not match this checkout and configuration")
    for name, resource in plan.resources.items():
        state_resource = state.resources.get(name)
        if not isinstance(state_resource, dict):
            raise InstallError("deployment state resource inventory is incomplete")
        for identity_key in ("name", "path", "role"):
            if identity_key in resource and state_resource.get(identity_key) != resource[identity_key]:
                raise InstallError("deployment state resource identity has drifted")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class DeploymentDoctor:
    """Collect structural checks without changing Docker or local state."""

    def __init__(self, docker: LifecycleDocker):
        self.docker = docker

    def run(self, config: InstallConfig, plan: DeploymentPlan) -> DoctorReport:
        checks: list[dict[str, str]] = []

        def check(name: str, operation) -> bool:
            try:
                operation()
            except Exception as exc:
                checks.append({"name": name, "status": "failed", "detail": str(exc)})
                return False
            checks.append({"name": name, "status": "ok", "detail": "verified"})
            return True

        store = StateStore(Path(config.state_dir))
        holder: dict[str, object] = {}

        def load_state() -> None:
            state = store.load()
            if state.status not in {"installed", "adopted"}:
                raise InstallError("deployment is not in an active installed state")
            holder["state"] = state

        if not check("state", load_state):
            return DoctorReport(False, checks)
        state = holder["state"]
        assert isinstance(state, DeploymentState)
        check("configuration", lambda: _state_matches_plan(state, config, plan))
        check("docker", self.docker.require_available)

        def verify_cwa() -> None:
            cwa = self.docker.inspect_container(config.cwa_container)
            if (
                cwa is None
                or cwa.get("State", {}).get("Status") != "running"
                or not _has_exact_cwa_version(cwa, config.cwa_version)
                or config.cwa_network not in _container_networks(cwa)
            ):
                raise InstallError("external CWA runtime evidence does not match")

        check("external-cwa", verify_cwa)
        verifier = ComposeInstaller(self.docker)
        image_holder: dict[str, str] = {}

        def verify_image() -> None:
            image_holder["id"] = verifier._verify_image(
                config, self.docker.inspect_image(config.image)
            )

        image_ok = check("image", verify_image)
        if image_ok:
            for role in ("api", "proxy"):
                def verify_role(role=role) -> None:
                    container_id, container = verifier._verify_container(
                        config, plan, state.install_id, role, image_holder["id"]
                    )
                    if state.resources[role].get("id") != container_id:
                        raise InstallError(f"{role} runtime ID does not match state")
                    if container.get("State", {}).get("Health", {}).get("Status") != "healthy":
                        raise InstallError(f"{role} runtime is not healthy")

                check(f"runtime-{role}", verify_role)

        def verify_private_network() -> None:
            name = str(plan.resources["private_network"]["name"])
            network = self.docker.inspect_network(name)
            labels = network.get("Labels", {}) if network else {}
            if (
                network is None
                or network.get("Id") != state.resources["private_network"].get("id")
                or network.get("Internal") is not True
                or labels.get("io.cwa-translate.install-id") != state.install_id
                or labels.get("io.cwa-translate.role") != "private-network"
                or labels.get("io.cwa-translate.revision") != config.identity.sha
            ):
                raise InstallError("private network ownership or isolation has drifted")

        check("private-network", verify_private_network)

        def verify_data() -> None:
            path = Path(config.data_dir)
            if path.is_symlink() or not path.is_dir() or _mode(path) & 0o077:
                raise InstallError("translation data directory is missing or not private")

        check("data-directory", verify_data)

        def verify_artifacts() -> None:
            if config.install_profile == "compose-existing":
                path = Path(config.state_dir) / "deployment.compose.json"
                if path.is_symlink() or _mode(path) != 0o600:
                    raise InstallError("Compose artifact is missing or not private")
                if json.loads(path.read_text(encoding="utf-8")) != render_compose(
                    config, plan, state.install_id
                ):
                    raise InstallError("Compose artifact does not match the plan")
            else:
                expected_templates = render_templates(config, plan)
                state_dir = Path(config.state_dir)
                expected_env = {
                    "api": _environment_text(
                        {**config.api_environment(), "BT_ROLE": "api"}
                    ),
                    "proxy": _environment_text({
                        **config.proxy_environment(),
                        "BT_ROLE": "proxy",
                        "BT_API_UPSTREAM": f"http://{plan.resources['api']['name']}:8390",
                    }),
                }
                for role in ("api", "proxy"):
                    env_path = state_dir / f"{role}.env"
                    if (
                        env_path.is_symlink()
                        or _mode(env_path) != 0o600
                        or env_path.read_text(encoding="utf-8") != expected_env[role]
                    ):
                        raise InstallError(f"{role} environment artifact has drifted")
                    template_path = Path(plan.resources[f"{role}_template"]["path"])
                    if (
                        template_path.is_symlink()
                        or template_path.read_text(encoding="utf-8")
                        != expected_templates[role]
                    ):
                        raise InstallError(f"{role} Unraid template has drifted")
            resources = copy.deepcopy(state.resources)
            _verify_identity_edge_artifact(config, plan, resources)
            if config.auth_profile == "authentik-forwarded" and (
                resources["identity_edge_config"].get("sha256")
                != state.resources["identity_edge_config"].get("sha256")
            ):
                raise InstallError("identity-edge artifact digest does not match state")

        check("artifacts", verify_artifacts)
        return DoctorReport(
            all(item["status"] == "ok" for item in checks), checks
        )


class RuntimeUninstaller:
    """Remove only state-owned runtime objects while retaining data and evidence."""

    def __init__(self, docker: LifecycleDocker):
        self.docker = docker

    def _verify_owned_runtime(
        self,
        state: DeploymentState,
        config: InstallConfig,
        plan: DeploymentPlan,
        *,
        tolerate_missing: bool,
    ) -> None:
        for role in ("proxy", "api"):
            resource = state.resources[role]
            container = self.docker.inspect_container(str(resource["name"]))
            if container is None and (resource.get("removed") or tolerate_missing):
                continue
            labels = container.get("Config", {}).get("Labels", {}) if container else {}
            if (
                container is None
                or container.get("Id") != resource.get("id")
                or labels.get("io.cwa-translate.managed-by") != "btctl"
                or labels.get("io.cwa-translate.install-id") != state.install_id
                or labels.get("io.cwa-translate.role") != role
                or labels.get("io.cwa-translate.revision") != config.identity.sha
            ):
                raise InstallError(f"{role} ownership evidence has drifted")
        resource = state.resources["private_network"]
        network = self.docker.inspect_network(str(resource["name"]))
        if network is None and (resource.get("removed") or tolerate_missing):
            return
        labels = network.get("Labels", {}) if network else {}
        if (
            network is None
            or network.get("Id") != resource.get("id")
            or labels.get("io.cwa-translate.install-id") != state.install_id
            or labels.get("io.cwa-translate.role") != "private-network"
        ):
            raise InstallError("private network ownership evidence has drifted")

    def uninstall(
        self, config: InstallConfig, plan: DeploymentPlan
    ) -> DeploymentState:
        store = StateStore(Path(config.state_dir))
        state = store.load()
        if state.status in {"uninstalled", "rolled_back"}:
            return state
        if state.status not in {"installed", "adopted", "uninstalling"}:
            raise InstallError("deployment state cannot be uninstalled")
        _state_matches_plan(state, config, plan)
        retry = state.status == "uninstalling"
        self._verify_owned_runtime(
            state, config, plan, tolerate_missing=retry
        )

        if config.install_profile == "unraid":
            expected = render_templates(config, plan)
            for role in ("api", "proxy"):
                resource = state.resources[f"{role}_template"]
                path = Path(str(resource["path"]))
                if (resource.get("removed") or retry) and not path.exists():
                    continue
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.read_text(encoding="utf-8") != expected[role]
                ):
                    raise InstallError(f"{role} template ownership evidence has drifted")

        resources = copy.deepcopy(state.resources)
        current = replace(state, status="uninstalling", resources=resources)
        store.save(current)
        for role in ("proxy", "api"):
            resource = resources[role]
            name = str(resource["name"])
            if self.docker.inspect_container(name) is not None:
                self.docker.remove_container(name)
            resource["removed"] = True
            store.save(current)
        private = resources["private_network"]
        private_name = str(private["name"])
        if self.docker.inspect_network(private_name) is not None:
            self.docker.remove_network(private_name)
        private["removed"] = True
        store.save(current)

        if config.install_profile == "unraid":
            for role in ("api", "proxy"):
                resource = resources[f"{role}_template"]
                path = Path(str(resource["path"]))
                if path.exists():
                    path.unlink()
                resource["removed"] = True
                store.save(current)

        completed = replace(current, status="uninstalled")
        store.save(completed)
        return completed


class MigrationJournalStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "migration-v214.json"

    def save(self, payload: dict[str, object]) -> None:
        if self.state_dir.is_symlink() or self.path.is_symlink():
            raise InstallError("migration journal destination must not be a symbolic link")
        document = dict(payload)
        document["schema_version"] = _JOURNAL_SCHEMA
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".migration-v214.json.", dir=self.state_dir
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> dict[str, object]:
        if self.path.is_symlink():
            raise InstallError("migration journal must not be a symbolic link")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError("migration journal could not be read") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _JOURNAL_SCHEMA:
            raise InstallError("migration journal schema is unsupported")
        return payload


def _tree_manifest(root: Path) -> tuple[str, int]:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InstallError("migration data must not contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file():
            raise InstallError("migration data must contain only directories and files")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
        })
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), len(entries)


def _sqlite_integrity(data_dir: Path, *, checkpoint: bool) -> None:
    database_path = data_dir / "translations.db"
    if database_path.is_symlink() or not database_path.is_file():
        raise InstallError("legacy translations.db is missing")
    try:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            if checkpoint:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise InstallError("SQLite integrity check failed")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise InstallError("SQLite integrity check failed") from exc


def _secure_copy(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise InstallError("migration target must not already exist")
    _tree_manifest(source)
    shutil.copytree(source, target, symlinks=True)
    for path in target.rglob("*"):
        if path.is_symlink():
            raise InstallError("migration copy unexpectedly contains a symbolic link")
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chmod(target, 0o700)


def _paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve()
    right = second.resolve()
    return left == right or left in right.parents or right in left.parents


class LegacyUpgrade:
    """Move an exact stopped v2.1.4 data snapshot into the split v2.2 runtime."""

    def __init__(self, docker: LifecycleDocker):
        self.docker = docker

    @staticmethod
    def _legacy_values(values: dict[str, str]) -> tuple[str, Path]:
        name = values.get("BT_LEGACY_CONTAINER", "")
        raw_path = values.get("BT_LEGACY_DATA_DIR", "")
        if not _CONTAINER_RE.fullmatch(name):
            raise InstallError("BT_LEGACY_CONTAINER must be one exact container name")
        path = Path(raw_path)
        if not path.is_absolute() or ".." in path.parts or path == Path("/"):
            raise InstallError("BT_LEGACY_DATA_DIR must be an absolute non-root directory")
        return name, path

    def _verify_legacy(
        self, name: str, data_dir: Path, *, expected: dict[str, object] | None = None
    ) -> dict:
        container = self.docker.inspect_container(name)
        if container is None:
            raise InstallError("exact legacy container is missing")
        image_ref = container.get("Config", {}).get("Image", "")
        tag = image_ref.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if tag not in {"2.1.4", "v2.1.4"}:
            raise InstallError("legacy container is not pinned to exact v2.1.4")
        mounts = [
            mount
            for mount in container.get("Mounts", [])
            if mount.get("Destination") == "/app/data"
        ]
        if (
            len(mounts) != 1
            or mounts[0].get("Type") != "bind"
            or Path(str(mounts[0].get("Source", ""))).resolve() != data_dir.resolve()
        ):
            raise InstallError("legacy /app/data bind mount does not match")
        if expected and (
            container.get("Id") != expected.get("legacy_container_id")
            or container.get("Image") != expected.get("legacy_image_id")
            or image_ref != expected.get("legacy_image_ref")
        ):
            raise InstallError("legacy runtime identity no longer matches the journal")
        return container

    def upgrade(
        self,
        config: InstallConfig,
        plan: DeploymentPlan,
        repository: Path,
        values: dict[str, str],
    ) -> DeploymentState:
        store = StateStore(Path(config.state_dir))
        journal_store = MigrationJournalStore(Path(config.state_dir))
        if store.path.exists() or journal_store.path.exists():
            raise InstallError("upgrade requires no existing state or migration journal")
        name, legacy_data = self._legacy_values(values)
        if legacy_data.is_symlink() or not legacy_data.is_dir():
            raise InstallError("BT_LEGACY_DATA_DIR must be a real directory")
        if legacy_data.resolve() == Path(config.data_dir).resolve():
            raise InstallError("legacy and target data directories must differ")
        repository_root = Path(repository).resolve()
        configured_backup = Path(config.backup_dir)
        if configured_backup.is_symlink():
            raise InstallError("BT_BACKUP_DIR must not be a symbolic link")
        backup_root = configured_backup.resolve()
        if backup_root == repository_root or repository_root in backup_root.parents:
            raise InstallError("BT_BACKUP_DIR must be outside the Git checkout")
        target = Path(config.data_dir)
        if any(
            _paths_overlap(left, right)
            for left, right in (
                (legacy_data, target),
                (legacy_data, backup_root),
                (target, backup_root),
            )
        ):
            raise InstallError(
                "legacy, target, and backup directories must not overlap"
            )

        legacy = self._verify_legacy(name, legacy_data)
        legacy_id = str(legacy.get("Id", ""))
        legacy_image_id = str(legacy.get("Image", ""))
        if not legacy_id or not legacy_image_id:
            raise InstallError("legacy container identity is incomplete")
        initial_status = legacy.get("State", {}).get("Status")
        if initial_status not in {"running", "exited", "created"}:
            raise InstallError("legacy container is not in a migratable state")

        installer = (
            ComposeInstaller(self.docker)
            if config.install_profile == "compose-existing"
            else UnraidInstaller(self.docker)
        )
        installer._preflight(config, plan)
        if target.exists() and any(target.iterdir()):
            raise InstallError("BT_DATA_DIR must be empty before migration")
        if target.exists():
            target.rmdir()

        suffix = hashlib.sha256(legacy_id.encode("utf-8")).hexdigest()[:12]
        snapshot = backup_root / f"pre-v2.2.0-{suffix}"
        backup_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(backup_root, 0o700)
        if snapshot.exists() or snapshot.is_symlink():
            raise InstallError("migration snapshot already exists")

        stopped = initial_status == "running"
        journal: dict[str, object] = {
            "status": "prepared",
            "legacy_container": name,
            "legacy_container_id": legacy_id,
            "legacy_image_id": legacy_image_id,
            "legacy_image_ref": legacy["Config"]["Image"],
            "legacy_data_dir": str(legacy_data),
            "legacy_initial_status": initial_status,
            "snapshot_path": str(snapshot),
            "target_data_dir": str(target),
        }
        journal_store.save(journal)
        target_installed = False
        try:
            if stopped:
                self.docker.stop_container(name)
            stopped_legacy = self._verify_legacy(name, legacy_data)
            if stopped_legacy.get("State", {}).get("Status") == "running":
                raise InstallError("legacy writer did not stop")
            _sqlite_integrity(legacy_data, checkpoint=True)
            source_manifest, source_files = _tree_manifest(legacy_data)
            _secure_copy(legacy_data, snapshot)
            _sqlite_integrity(snapshot, checkpoint=False)
            snapshot_manifest, snapshot_files = _tree_manifest(snapshot)
            if (source_manifest, source_files) != (snapshot_manifest, snapshot_files):
                raise InstallError("offline snapshot does not match the stopped source")
            _secure_copy(snapshot, target)
            _sqlite_integrity(target, checkpoint=False)
            target_manifest, target_files = _tree_manifest(target)
            if (snapshot_manifest, snapshot_files) != (target_manifest, target_files):
                raise InstallError("migration target does not match the snapshot")

            journal.update({
                "status": "snapshot-complete",
                "snapshot_manifest": snapshot_manifest,
                "snapshot_files": snapshot_files,
                "target_manifest": target_manifest,
            })
            journal_store.save(journal)
            state = installer.install(config, plan, Path(repository))
            target_installed = True
            journal.update({"status": "upgraded", "install_id": state.install_id})
            journal_store.save(journal)
            return state
        except BaseException as exc:
            journal["status"] = (
                "upgrade-journal-failed" if target_installed else "upgrade-failed"
            )
            if stopped and not target_installed:
                try:
                    current = self.docker.inspect_container(name)
                    if current and current.get("State", {}).get("Status") != "running":
                        self.docker.start_container(name)
                except BaseException:
                    pass
            try:
                journal_store.save(journal)
            except BaseException:
                pass
            if isinstance(exc, InstallError):
                raise
            if target_installed:
                raise InstallError(
                    "v2.2 started but the migration journal could not be committed; "
                    "legacy remains stopped"
                ) from exc
            raise InstallError("legacy upgrade failed") from exc

    def rollback(
        self, config: InstallConfig, plan: DeploymentPlan
    ) -> DeploymentState:
        journal_store = MigrationJournalStore(Path(config.state_dir))
        journal = journal_store.load()
        store = StateStore(Path(config.state_dir))
        if journal.get("status") == "rolled_back":
            return store.load()
        if journal.get("status") not in {"upgraded", "rollback-failed"}:
            raise InstallError("migration journal is not eligible for rollback")
        legacy_data = Path(str(journal.get("legacy_data_dir", "")))
        name = str(journal.get("legacy_container", ""))
        legacy = self._verify_legacy(name, legacy_data, expected=journal)
        source_manifest, source_files = _tree_manifest(legacy_data)
        if (
            source_manifest != journal.get("snapshot_manifest")
            or source_files != journal.get("snapshot_files")
        ):
            raise InstallError("legacy source changed after the offline snapshot")
        snapshot = Path(str(journal.get("snapshot_path", "")))
        snapshot_manifest, snapshot_files = _tree_manifest(snapshot)
        if (
            snapshot_manifest != journal.get("snapshot_manifest")
            or snapshot_files != journal.get("snapshot_files")
        ):
            raise InstallError("migration snapshot integrity has drifted")

        try:
            state = RuntimeUninstaller(self.docker).uninstall(config, plan)
            current = self.docker.inspect_container(name)
            if current is None:
                raise InstallError("legacy container disappeared during rollback")
            if current.get("State", {}).get("Status") != "running":
                self.docker.start_container(name)
            running = self._verify_legacy(name, legacy_data, expected=journal)
            if running.get("State", {}).get("Status") != "running":
                raise InstallError("legacy container did not restart")
            completed = replace(state, status="rolled_back")
            store.save(completed)
            journal["status"] = "rolled_back"
            journal_store.save(journal)
            return completed
        except BaseException:
            journal["status"] = "rollback-failed"
            journal_store.save(journal)
            raise
