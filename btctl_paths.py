"""Shared fail-closed host-path policy for managed Unraid operations."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Mapping, Protocol

from btctl_core import ConfigError


TEMPLATE_ROOT = Path("/boot/config/plugins/dockerMan/templates-user")


class UnraidPathConfig(Protocol):
    install_profile: str
    state_dir: str
    data_dir: str
    backup_dir: str
    unraid_template_dir: str


def validate_mount_path(path: Path, label: str) -> Path:
    value = str(path)
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise ConfigError(f"{label} must be one absolute non-root path")
    if any(character in value for character in (",", "\n", "\r", "\t", "\0")):
        raise ConfigError(f"{label} contains a character unsupported by Docker mount")
    return path


def _validate_existing_components(path: Path) -> None:
    current = Path(path.anchor)
    missing = False
    for component in path.parts[1:]:
        current /= component
        if missing:
            continue
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        except OSError as exc:
            raise ConfigError("managed path components could not be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ConfigError("managed path must not contain a symbolic link")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError("managed path parent must be a real directory")
        if current == path and not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError("managed path must be a directory when it already exists")


def validate_storage_path(
    path: Path,
    label: str,
    *,
    storage_root: Path = Path("/mnt"),
) -> Path:
    candidate = validate_mount_path(Path(path), label)
    root = validate_mount_path(Path(storage_root), "Unraid storage root")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"{label} must be under {root}") from exc
    if not relative.parts:
        raise ConfigError(f"{label} must be below an existing Unraid share or pool root")
    if relative.parts[0] in {"user", "user0"}:
        if len(relative.parts) < 3:
            raise ConfigError(f"{label} must be below an existing Unraid share")
        storage_boundary = root / relative.parts[0] / relative.parts[1]
        boundary_description = "share"
    else:
        if len(relative.parts) < 2:
            raise ConfigError(
                f"{label} must be below an existing Unraid share or pool root"
            )
        storage_boundary = root / relative.parts[0]
        boundary_description = "pool root"
    try:
        metadata = storage_boundary.lstat()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"{label} must be below an existing Unraid {boundary_description}"
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"{label} Unraid {boundary_description} could not be inspected"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ConfigError(
            f"{label} Unraid {boundary_description} must be one real directory"
        )
    _validate_existing_components(candidate)
    return candidate


def validate_template_path(path: Path) -> Path:
    candidate = validate_mount_path(path, "BT_UNRAID_TEMPLATE_DIR")
    if candidate != TEMPLATE_ROOT:
        raise ConfigError(
            "BT_UNRAID_TEMPLATE_DIR must be exactly "
            "/boot/config/plugins/dockerMan/templates-user"
        )
    _validate_existing_components(candidate)
    return candidate


def storage_minimum(path: Path) -> Path:
    relative = path.relative_to(Path("/mnt"))
    if relative.parts[0] in {"user", "user0"}:
        return Path("/mnt") / relative.parts[0] / relative.parts[1]
    return Path("/mnt") / relative.parts[0]


def paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def validate_unraid_config_paths(
    config: UnraidPathConfig,
    values: Mapping[str, str],
    repository: Path,
) -> None:
    if config.install_profile != "unraid":
        return
    paths = {
        "state": validate_storage_path(Path(config.state_dir), "BT_STATE_DIR"),
        "data": validate_storage_path(Path(config.data_dir), "BT_DATA_DIR"),
        "backup": validate_storage_path(Path(config.backup_dir), "BT_BACKUP_DIR"),
        "template": validate_template_path(Path(config.unraid_template_dir)),
    }
    legacy = values.get("BT_LEGACY_DATA_DIR", "")
    if legacy:
        paths["legacy"] = validate_storage_path(
            Path(legacy), "BT_LEGACY_DATA_DIR"
        )
    checkout = validate_mount_path(repository, "repository")
    for label, managed in paths.items():
        if paths_overlap(checkout, managed):
            raise ConfigError(f"{label} path must not overlap the Git checkout")
