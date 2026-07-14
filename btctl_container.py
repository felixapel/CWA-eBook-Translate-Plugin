#!/usr/bin/env python3
"""Least-privilege mount planning for the stock-Unraid btctl dispatcher."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from btctl import _load_values, _release_identity
from btctl_compose import InstallError
from btctl_core import ConfigError, InstallConfig
from btctl_lifecycle import MigrationJournalStore
from btctl_paths import (
    paths_overlap as _paths_overlap,
    storage_minimum as _storage_minimum,
    validate_mount_path as _validate_mount_text,
    validate_storage_path,
    validate_template_path as _validate_template_path,
)


EX_USAGE = 64
LOCK_DESTINATION = Path("/run/btctl-lock")
HOST_LOCK_SOURCE = Path("/run/cwa-translate-btctl-locks")
_COMMAND_ACCESS: dict[str, tuple[tuple[str, str], ...]] = {
    "plan": (),
    "auth-snippet": (),
    "doctor": (("state", "ro"), ("data", "ro"), ("template", "ro")),
    "adopt": (("state", "rw"), ("data", "ro"), ("template", "ro")),
    "install": (("state", "rw"), ("data", "rw"), ("template", "rw")),
    "uninstall": (("state", "rw"), ("template", "rw")),
    "upgrade": (
        ("state", "rw"),
        ("data", "rw"),
        ("backup", "rw"),
        ("legacy", "rw"),
        ("template", "rw"),
    ),
    "rollback": (
        ("state", "rw"),
        ("data", "ro"),
        ("backup", "ro"),
        ("legacy", "ro"),
        ("template", "rw"),
    ),
}


@dataclass(frozen=True, slots=True)
class MountSpec:
    path: Path
    mode: str

    def __post_init__(self) -> None:
        if self.mode not in {"ro", "rw"}:
            raise ConfigError("mount mode must be ro or rw")
        _validate_mount_text(self.path, "mount path")


@dataclass(frozen=True, slots=True)
class MountPlan:
    command: str
    socket: bool
    mounts: tuple[MountSpec, ...]
    lock_source: Path | None = None

    def __post_init__(self) -> None:
        if self.lock_source is not None:
            _validate_mount_text(self.lock_source, "lock mount source")

    def render(self) -> str:
        lines = [f"BTCTL_MOUNT_PLAN\t1\t{self.command}\tunraid"]
        lines.extend(f"mount\t{mount.mode}\t{mount.path}" for mount in self.mounts)
        if self.lock_source is not None:
            lines.append(
                f"lock\tro\t{self.lock_source}\t{LOCK_DESTINATION}"
            )
        lines.append(f"socket\t{'yes' if self.socket else 'no'}")
        return "\n".join(lines) + "\n"


def command_path_access(command: str) -> tuple[tuple[str, str], ...]:
    try:
        return _COMMAND_ACCESS[command]
    except KeyError as exc:
        raise ConfigError("unsupported containerized btctl command") from exc


def command_requires_socket(command: str) -> bool:
    command_path_access(command)
    return command not in {"plan", "auth-snippet"}


def _nearest_existing_directory(path: Path, minimum: Path) -> Path:
    candidate = path
    while True:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if candidate == minimum:
                raise ConfigError("managed mount root does not exist")
            candidate = candidate.parent
            continue
        except OSError as exc:
            raise ConfigError("managed mount source could not be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError("managed mount source must be one real directory")
        return candidate


def mount_source_for_path(
    command: str,
    name: str,
    path: Path,
    minimum: Path,
) -> Path:
    required = path.parent if name == "state" or (
        command == "upgrade" and name == "data"
    ) else path
    return _nearest_existing_directory(required, minimum)


def legacy_data_path(
    command: str,
    config: InstallConfig,
    values: Mapping[str, str],
) -> str:
    if command != "rollback":
        return values.get("BT_LEGACY_DATA_DIR", "")
    try:
        journal = MigrationJournalStore(Path(config.state_dir)).load()
    except (InstallError, OSError) as exc:
        raise ConfigError("rollback requires a readable migration journal") from exc
    value = journal.get("legacy_data_dir", "")
    if not isinstance(value, str) or not value:
        raise ConfigError("rollback migration journal has no legacy data path")
    return value


def _validate_repository(repository: Path) -> Path:
    candidate = _validate_mount_text(repository, "repository")
    if candidate.is_symlink() or not candidate.is_dir():
        raise ConfigError("repository must be one real directory")
    git_dir = candidate / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ConfigError("containerized btctl requires a full Git checkout")
    return candidate


def _config_for_command(
    command: str,
    repository: Path,
    env_file: Path,
    expected_revision: str,
) -> tuple[InstallConfig, Mapping[str, str]]:
    identity = _release_identity(repository)
    if identity.sha != expected_revision:
        raise ConfigError("mount plan revision does not match the verified checkout")
    values = _load_values(env_file)
    legacy_plan = bool(
        values.get("BT_LEGACY_CONTAINER") and values.get("BT_LEGACY_DATA_DIR")
    )
    allow_legacy = command in {"doctor", "uninstall", "upgrade", "rollback"}
    if command == "plan":
        allow_legacy = legacy_plan
    config = InstallConfig.from_mapping(
        values,
        identity,
        allow_legacy_cwa=allow_legacy,
    )
    if config.install_profile != "unraid":
        raise ConfigError(
            "the containerized fallback supports only BT_INSTALL_PROFILE=unraid; "
            "compose-existing requires host Python 3.11+"
        )
    return config, values


def create_mount_plan(
    command: str,
    repository: Path,
    env_file: Path,
    expected_revision: str,
) -> MountPlan:
    access = command_path_access(command)
    checkout = _validate_repository(repository)
    environment = _validate_mount_text(env_file, "environment file")
    if environment.is_symlink() or not environment.is_file():
        raise ConfigError("environment file must be one real regular file")
    config, values = _config_for_command(
        command, checkout, environment, expected_revision
    )

    paths: dict[str, Path] = {
        "state": validate_storage_path(Path(config.state_dir), "BT_STATE_DIR"),
        "data": validate_storage_path(Path(config.data_dir), "BT_DATA_DIR"),
        "backup": validate_storage_path(Path(config.backup_dir), "BT_BACKUP_DIR"),
        "template": _validate_template_path(Path(config.unraid_template_dir)),
    }
    legacy_text = legacy_data_path(command, config, values)
    if legacy_text:
        paths["legacy"] = validate_storage_path(
            Path(legacy_text), "BT_LEGACY_DATA_DIR"
        )

    for label, managed in paths.items():
        if _paths_overlap(checkout, managed):
            raise ConfigError(f"{label} path must not overlap the Git checkout")
    overlap_names = list(paths)
    for index, left_name in enumerate(overlap_names):
        for right_name in overlap_names[index + 1 :]:
            if _paths_overlap(paths[left_name], paths[right_name]):
                raise ConfigError(
                    f"{left_name} and {right_name} paths must not overlap"
                )

    mounts: dict[Path, str] = {checkout: "ro", environment: "ro"}

    def add_mount(source: Path, mode: str) -> None:
        previous = mounts.get(source)
        if previous is not None and previous != mode:
            raise ConfigError(
                "least-privilege mounts cannot represent overlapping read/write paths"
            )
        mounts[source] = mode

    access_modes = dict(access)
    lock_source: Path | None = None
    if "state" in access_modes:
        lock_source = HOST_LOCK_SOURCE

    for name, mode in access:
        if name not in paths:
            raise ConfigError(f"{command} requires BT_LEGACY_DATA_DIR")
        desired = paths[name]
        minimum = Path("/boot") if name == "template" else _storage_minimum(desired)
        source = mount_source_for_path(command, name, desired, minimum)
        if name == "state" and desired.exists():
            add_mount(desired, mode)
        elif name == "state" and mode == "ro":
            # The dedicated lock mount is enough to serialize a diagnostic
            # against a missing state directory. Avoid exposing its siblings.
            continue
        else:
            add_mount(source, mode)

    # A missing writable state directory or an upgrade staging directory can
    # require an ancestor bind. Override that broad bind for every existing
    # managed path that this command must only read (or must not access).
    writable_sources = tuple(
        source for source, mode in mounts.items() if mode == "rw"
    )
    for name, managed in paths.items():
        if not managed.exists() or access_modes.get(name) == "rw":
            continue
        if any(source in managed.parents for source in writable_sources):
            add_mount(managed, "ro")

    ordered = tuple(
        MountSpec(path, mode)
        for path, mode in sorted(
            mounts.items(), key=lambda item: (len(item[0].parts), str(item[0]))
        )
    )
    return MountPlan(
        command=command,
        socket=command_requires_socket(command),
        mounts=ordered,
        lock_source=lock_source,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--command", required=True)
    parser.add_argument("--expected-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        plan = create_mount_plan(
            arguments.command,
            arguments.repository,
            arguments.env,
            arguments.expected_revision,
        )
        print(plan.render(), end="")
        return 0
    except ConfigError as exc:
        print(f"btctl: configuration error: {exc}", file=sys.stderr)
        return EX_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
