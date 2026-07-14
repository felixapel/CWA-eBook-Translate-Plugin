"""Pure configuration contracts shared by the source-only installer and tests."""

from __future__ import annotations

import fcntl
import ipaddress
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NETWORK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$"
)
_SECRET_NAME_RE = re.compile(r"(?:KEY|PASSWORD|SECRET|TOKEN)$")
_INSTALL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_INSTALL_PROFILES = frozenset({"unraid", "compose-existing"})
_INGRESS_MODES = frozenset({"published", "docker-edge"})
_AUTH_PROFILES = frozenset({"cwa-session", "authentik-forwarded"})
_REVERSE_PROXIES = frozenset({"nginx", "traefik", "caddy"})
_LLM_PROVIDERS = frozenset(
    {
        "local",
        "openai",
        "anthropic",
        "gemini",
        "groq",
        "together",
        "minimax",
        "deepseek",
        "openrouter",
    }
)
STATE_SCHEMA_VERSION = 1

_MANAGED_PROXY_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "forwarded",
        "host",
        "transfer-encoding",
        "upgrade",
        "x-authentik-groups",
        "x-authentik-uid",
        "x-bt-roles",
        "x-bt-subject",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
        "x-real-ip",
    }
)


class ConfigError(ValueError):
    """An installation value is ambiguous, unsafe, or incomplete."""


def _fsync_directory(path: Path) -> None:
    """Flush one real directory without following its final path component."""
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ConfigError("durability target must be a directory")
        os.fsync(descriptor)
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError("directory metadata could not be made durable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def ensure_directory_durable(
    path: Path,
    *,
    mode: int = 0o700,
    enforce_existing_mode: bool = True,
) -> None:
    """Create a directory tree and durably publish every new path entry."""
    destination = Path(path)
    if mode not in {0o700, 0o755}:
        raise ConfigError("managed directory mode is unsupported")
    missing: list[Path] = []
    cursor = destination
    try:
        while not cursor.exists():
            if cursor.is_symlink():
                raise ConfigError("managed directory must not be a symbolic link")
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise ConfigError("managed directory has no existing ancestor")
            cursor = parent
        if cursor.is_symlink() or not cursor.is_dir():
            raise ConfigError("managed directory ancestor must be a real directory")

        for directory in reversed(missing):
            try:
                directory.mkdir(mode=mode)
            except FileExistsError:
                pass
            if directory.is_symlink() or not directory.is_dir():
                raise ConfigError("managed directory must be a real directory")
            if enforce_existing_mode:
                os.chmod(directory, mode)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)

        if not missing:
            if destination.is_symlink() or not destination.is_dir():
                raise ConfigError("managed directory must be a real directory")
            if enforce_existing_mode:
                os.chmod(destination, mode)
            _fsync_directory(destination)
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError("managed directory could not be created durably") from exc


def read_private_text(directory: Path, filename: str, *, label: str) -> str:
    """Read one installer-owned evidence file without following links.

    Lifecycle evidence is authoritative only while both its directory and file
    remain owned by the invoking account and exactly private. Opening the file
    relative to the validated directory descriptor also closes the usual
    check-then-open symlink race.
    """
    directory_fd: int | None = None
    descriptor: int | None = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(directory, directory_flags)
        directory_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or stat.S_IMODE(directory_metadata.st_mode) != 0o700
            or directory_metadata.st_uid != os.geteuid()
        ):
            raise ConfigError(
                f"{label} directory must be owned by the current user with mode 0700"
            )

        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(filename, file_flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != directory_metadata.st_uid
        ):
            raise ConfigError(
                f"{label} must be one private regular file owned by the current user "
                "with mode 0600"
            )
        if metadata.st_size > 1024 * 1024:
            raise ConfigError(f"{label} exceeds the 1 MiB safety limit")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            return handle.read()
    except ConfigError:
        raise
    except FileNotFoundError as exc:
        raise ConfigError(f"{label} does not exist") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{label} could not be read securely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


class OperationLock:
    """Lock the state directory without creating a separate filesystem object."""

    def __init__(self, state_dir: Path, *, create: bool = True):
        self.state_dir = Path(state_dir)
        container_lock = os.environ.get("BTCTL_LOCK_DIRECTORY", "")
        if container_lock:
            lock_target = Path(container_lock)
            if (
                not lock_target.is_absolute()
                or lock_target == Path("/")
                or ".." in lock_target.parts
            ):
                raise ConfigError("container lifecycle lock directory is invalid")
            self.lock_target = lock_target
        else:
            self.lock_target = self.state_dir.parent
        self.create = create
        self._descriptor: int | None = None

    def __enter__(self) -> "OperationLock":
        if self.state_dir.is_symlink() or self.lock_target.is_symlink():
            raise ConfigError("lifecycle lock destination must not be a symbolic link")
        try:
            if self.create:
                self.lock_target.mkdir(parents=True, mode=0o700, exist_ok=True)
            elif not self.lock_target.is_dir():
                raise ConfigError(
                    "lifecycle lock parent does not exist for read-only locking"
                )
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_target, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise ConfigError("lifecycle lock target must be a directory")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigError(
                    "another btctl lifecycle operation is already in progress"
                ) from exc
        except BaseException:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _clean_value(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    if value != value.strip() or "\n" in value or "\r" in value or "\0" in value:
        raise ConfigError(f"{name} must be a clean single-line value")
    if not value and not allow_empty:
        raise ConfigError(f"{name} is required")
    return value


def _choice(values: Mapping[str, str], name: str, allowed: frozenset[str]) -> str:
    value = _clean_value(values.get(name, ""), name)
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {sorted(allowed)}")
    return value


def _validated_http_parts(value: object, name: str):
    cleaned = _clean_value(value, name)
    if not cleaned.isascii() or any(
        ord(character) < 33 or ord(character) == 127 for character in cleaned
    ):
        raise ConfigError(f"{name} must be a clean ASCII http(s) URL")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{name} must be an http(s) URL") from exc
    hostname = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigError(f"{name} must be an http(s) URL with one exact authority")
    try:
        ipaddress.ip_address(hostname)
        is_ipv6 = ":" in hostname
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(hostname):
            raise ConfigError(f"{name} contains an invalid hostname")
        is_ipv6 = False
    normalized_host = f"[{hostname}]" if is_ipv6 else hostname
    if port is not None:
        normalized_host += f":{port}"
    if parsed.netloc.casefold() != normalized_host.casefold():
        raise ConfigError(f"{name} must contain only one exact host and optional port")
    return parsed, f"{parsed.scheme}://{normalized_host}"


def _exact_origin(value: object, name: str) -> str:
    parsed, normalized = _validated_http_parts(value, name)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must be an exact http(s) origin")
    return normalized


def _http_url(value: object, name: str) -> str:
    parsed, normalized = _validated_http_parts(value, name)
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must be an absolute http(s) URL")
    return f"{normalized}{parsed.path}"


def _exact_peer(value: object) -> str:
    cleaned = _clean_value(value, "BT_IDENTITY_PROXY_IP")
    try:
        network = ipaddress.ip_network(cleaned, strict=True)
    except ValueError as exc:
        raise ConfigError(
            "BT_IDENTITY_PROXY_IP must be one exact /32 or /128 peer"
        ) from exc
    if network.prefixlen != network.max_prefixlen:
        raise ConfigError("BT_IDENTITY_PROXY_IP must be one exact /32 or /128 peer")
    return str(network)


def _cwa_identity_header(value: object) -> str:
    cleaned = _clean_value(value, "BT_CWA_IDENTITY_HEADER")
    if not cleaned.isascii() or not _HEADER_NAME_RE.fullmatch(cleaned):
        raise ConfigError(
            "BT_CWA_IDENTITY_HEADER must be one bounded HTTP header name"
        )
    if cleaned.casefold() in _MANAGED_PROXY_HEADERS:
        raise ConfigError(
            "BT_CWA_IDENTITY_HEADER conflicts with a proxy-managed security header"
        )
    return cleaned


def _absolute_dir(value: object, name: str) -> str:
    cleaned = _clean_value(value, name)
    path = Path(cleaned)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise ConfigError(f"{name} must be an absolute non-root directory")
    return str(path)


def _require_disjoint_directories(paths: Mapping[str, str]) -> None:
    items = [(name, Path(value)) for name, value in paths.items()]
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ConfigError(
                    f"managed directories must not overlap: {left_name} and {right_name}"
                )


def _optional_port(value: object, name: str) -> int | None:
    cleaned = _clean_value(value, name, allow_empty=True)
    if not cleaned:
        return None
    if not cleaned.isdecimal() or not 1 <= int(cleaned) <= 65535:
        raise ConfigError(f"{name} must be empty or an integer from 1 to 65535")
    return int(cleaned)


def _validate_authentik_version(value: object) -> str:
    cleaned = _clean_value(value, "BT_AUTHENTIK_VERSION")
    match = re.fullmatch(r"(20[0-9]{2})\.([0-9]{1,2})\.([0-9]+)", cleaned)
    if not match:
        raise ConfigError("BT_AUTHENTIK_VERSION must be YYYY.MINOR.PATCH")
    year, minor, patch = (int(part) for part in match.groups())
    # Authentik supports only its two newest release branches and only their
    # latest patch. Keep a release-time fail-closed floor here instead of
    # claiming that one historical CVE denylist proves an identity authority
    # is safe. These floors match the official policy snapshot for v2.2.0.
    supported_security_floors = {
        (2026, 2): 5,
        (2026, 5): 4,
    }
    minimum_patch = supported_security_floors.get((year, minor))
    if minimum_patch is None or patch < minimum_patch:
        raise ConfigError(
            "BT_AUTHENTIK_VERSION is below the v2.2.0 supported security floor; "
            "use Authentik 2026.2.5+ or 2026.5.4+ and confirm the latest patch "
            "in Authentik's official security policy"
        )
    return cleaned


def compatibility_tier(version: str) -> str:
    """Return the documented CWA support tier or reject an unknown release."""
    cleaned = _clean_value(version, "BT_CWA_VERSION")
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", cleaned)
    if not match:
        raise ConfigError("BT_CWA_VERSION must be an exact stable semantic version")
    major, minor, patch = (int(part) for part in match.groups())
    if major == 4:
        return "tier1"
    if (major, minor, patch) == (3, 1, 4):
        return "legacy"
    raise ConfigError(
        "unsupported CWA version: Tier 1 is 4.x; legacy support is exactly 3.1.4"
    )


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    version: str
    sha: str

    @classmethod
    def from_checkout(cls, *, version: str, sha: str, clean: bool) -> "ReleaseIdentity":
        if not clean:
            raise ConfigError("installation requires a clean checkout")
        if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
            raise ConfigError("VERSION must contain an exact semantic version")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise ConfigError("git SHA must be a full lowercase 40-character digest")
        return cls(version=version, sha=sha)

    @property
    def image(self) -> str:
        return f"local/cwa-translate:{self.version}-{self.sha[:12]}"


def release_identity_from_checkout(repository: Path) -> ReleaseIdentity:
    """Read an immutable identity from one exact, clean Git checkout."""
    root = Path(repository)
    if root.is_symlink() or not root.is_dir():
        raise ConfigError("repository must be a real directory")
    git_environment = os.environ.copy()
    git_environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    git_environment["GIT_OPTIONAL_LOCKS"] = "0"
    git_prefix = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(root),
    ]
    try:
        top_level = subprocess.run(
            [*git_prefix, "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_environment,
        ).stdout.strip()
        if Path(top_level).resolve() != root.resolve():
            raise ConfigError("repository must be the Git checkout root")
        subprocess.run(
            [*git_prefix, "ls-files", "--error-unmatch", "VERSION"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_environment,
        )
        sha = subprocess.run(
            [*git_prefix, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_environment,
        ).stdout.strip()
        dirty = subprocess.run(
            [
                *git_prefix,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_environment,
        ).stdout
        version_path = root / "VERSION"
        if version_path.is_symlink():
            raise ConfigError("VERSION must not be a symbolic link")
        version = version_path.read_text(encoding="utf-8").strip()
    except ConfigError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ConfigError("could not inspect the Git checkout") from exc
    return ReleaseIdentity.from_checkout(version=version, sha=sha, clean=not dirty)


@dataclass(frozen=True, slots=True)
class InstallConfig:
    identity: ReleaseIdentity
    install_profile: str
    install_name: str
    ingress_mode: str
    auth_profile: str
    public_origin: str
    cwa_upstream: str
    cwa_container: str
    cwa_network: str
    cwa_version: str
    compatibility_tier: str
    cwa_identity_header: str
    edge_network: str
    state_dir: str
    data_dir: str
    backup_dir: str
    unraid_template_dir: str
    proxy_port: int | None
    identity_proxy_peer: str
    authentik_version: str
    authentik_outpost_url: str
    reverse_proxy: str
    llm_provider: str
    llm_model: str
    local_url: str
    llm_api_key: str

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        identity: ReleaseIdentity,
        *,
        allow_legacy_cwa: bool = False,
    ) -> "InstallConfig":
        install_profile = _choice(
            values, "BT_INSTALL_PROFILE", _INSTALL_PROFILES
        )
        install_name = _clean_value(
            values.get("BT_INSTALL_NAME", "cwa-translate"), "BT_INSTALL_NAME"
        )
        if not _NETWORK_RE.fullmatch(install_name):
            raise ConfigError("BT_INSTALL_NAME must be a bounded Docker-safe name")
        ingress_mode = _choice(values, "BT_INGRESS_MODE", _INGRESS_MODES)
        auth_profile = _choice(values, "BT_AUTH_PROFILE", _AUTH_PROFILES)
        public_origin = _exact_origin(values.get("BT_PUBLIC_ORIGIN", ""), "BT_PUBLIC_ORIGIN")
        cwa_upstream = _exact_origin(values.get("CWA_UPSTREAM", ""), "CWA_UPSTREAM")

        cwa_container = _clean_value(
            values.get("BT_CWA_CONTAINER", ""), "BT_CWA_CONTAINER"
        )
        if not _CONTAINER_RE.fullmatch(cwa_container):
            raise ConfigError("BT_CWA_CONTAINER must be one exact Docker container name")
        parsed_cwa_upstream = urlsplit(cwa_upstream)
        if (
            parsed_cwa_upstream.scheme != "http"
            or parsed_cwa_upstream.hostname is None
            or parsed_cwa_upstream.hostname.casefold() != cwa_container.casefold()
            or parsed_cwa_upstream.port != 8083
        ):
            raise ConfigError(
                "CWA_UPSTREAM must be exactly http://<BT_CWA_CONTAINER>:8083"
            )
        cwa_upstream = f"http://{cwa_container}:8083"
        cwa_network = _clean_value(
            values.get("BT_CWA_NETWORK", ""), "BT_CWA_NETWORK"
        )
        if not _NETWORK_RE.fullmatch(cwa_network):
            raise ConfigError("BT_CWA_NETWORK must be one exact Docker network name")
        cwa_version = _clean_value(
            values.get("BT_CWA_VERSION", ""), "BT_CWA_VERSION"
        )
        cwa_tier = compatibility_tier(cwa_version)
        if cwa_tier == "legacy" and not allow_legacy_cwa:
            raise ConfigError(
                "CWA 3.1.4 is migration-only; use the explicit btctl upgrade workflow"
            )
        cwa_identity_header = _cwa_identity_header(
            values.get("BT_CWA_IDENTITY_HEADER", "Remote-User")
        )
        state_dir = _absolute_dir(values.get("BT_STATE_DIR", ""), "BT_STATE_DIR")
        data_dir = _absolute_dir(values.get("BT_DATA_DIR", ""), "BT_DATA_DIR")
        backup_dir = _absolute_dir(values.get("BT_BACKUP_DIR", ""), "BT_BACKUP_DIR")
        if "," in data_dir:
            raise ConfigError("BT_DATA_DIR must not contain a comma")
        unraid_template_dir = _clean_value(
            values.get("BT_UNRAID_TEMPLATE_DIR", ""),
            "BT_UNRAID_TEMPLATE_DIR",
            allow_empty=True,
        )
        if install_profile == "unraid":
            unraid_template_dir = _absolute_dir(
                unraid_template_dir or "/boot/config/plugins/dockerMan/templates-user",
                "BT_UNRAID_TEMPLATE_DIR",
            )
            safe_unraid_path = re.compile(r"^/[A-Za-z0-9_./@:+-]+$")
            for path_name, path_value in (
                ("BT_STATE_DIR", state_dir),
                ("BT_DATA_DIR", data_dir),
                ("BT_BACKUP_DIR", backup_dir),
                ("BT_UNRAID_TEMPLATE_DIR", unraid_template_dir),
            ):
                if not safe_unraid_path.fullmatch(path_value):
                    raise ConfigError(
                        f"{path_name} contains characters unsafe for DockerMan"
                    )
        else:
            # Keep one example file switchable between profiles. This path has
            # no meaning and enters no runtime artifact outside Unraid.
            unraid_template_dir = ""
        managed_directories = {
            "BT_STATE_DIR": state_dir,
            "BT_DATA_DIR": data_dir,
            "BT_BACKUP_DIR": backup_dir,
        }
        if unraid_template_dir:
            managed_directories["BT_UNRAID_TEMPLATE_DIR"] = unraid_template_dir
        _require_disjoint_directories(managed_directories)

        proxy_port = _optional_port(values.get("BT_PROXY_PORT", ""), "BT_PROXY_PORT")
        edge_network = _clean_value(
            values.get("BT_EDGE_NETWORK", ""), "BT_EDGE_NETWORK", allow_empty=True
        )
        if ingress_mode == "published":
            if proxy_port is None:
                raise ConfigError("BT_PROXY_PORT is required for published ingress")
            if edge_network:
                raise ConfigError("BT_EDGE_NETWORK must be empty for published ingress")
        else:
            if proxy_port is not None:
                raise ConfigError("BT_PROXY_PORT must be empty for docker-edge ingress")
            if not edge_network or not _NETWORK_RE.fullmatch(edge_network):
                raise ConfigError(
                    "BT_EDGE_NETWORK must be one exact Docker network name for docker-edge ingress"
                )
        if edge_network and edge_network == cwa_network:
            raise ConfigError("BT_EDGE_NETWORK and BT_CWA_NETWORK must be separate")

        llm_provider = _choice(values, "LLM_PROVIDER", _LLM_PROVIDERS)
        llm_model = _clean_value(values.get("LLM_MODEL", ""), "LLM_MODEL")
        if not _TOKEN_RE.fullmatch(llm_model):
            raise ConfigError("LLM_MODEL contains unsupported characters")
        local_url = _clean_value(
            values.get("BT_LOCAL_URL", ""), "BT_LOCAL_URL", allow_empty=True
        )
        llm_api_key = _clean_value(
            values.get("LLM_API_KEY", ""), "LLM_API_KEY", allow_empty=True
        )
        if llm_provider == "local":
            if not local_url or llm_api_key:
                raise ConfigError(
                    "local provider requires BT_LOCAL_URL and forbids LLM_API_KEY"
                )
            local_url = _http_url(local_url, "BT_LOCAL_URL")
            if urlsplit(local_url).path != "/v1/chat/completions":
                raise ConfigError(
                    "BT_LOCAL_URL must target the exact /v1/chat/completions endpoint"
                )
        elif not llm_api_key or local_url:
            raise ConfigError(
                "cloud providers require LLM_API_KEY and forbid BT_LOCAL_URL"
            )

        identity_proxy_peer = ""
        authentik_version = ""
        authentik_outpost_url = ""
        reverse_proxy = ""
        if auth_profile == "authentik-forwarded":
            if ingress_mode != "docker-edge":
                raise ConfigError(
                    "authentik-forwarded requires BT_INGRESS_MODE=docker-edge"
                )
            identity_proxy_peer = _exact_peer(values.get("BT_IDENTITY_PROXY_IP", ""))
            authentik_version = _validate_authentik_version(
                values.get("BT_AUTHENTIK_VERSION", "")
            )
            reverse_proxy = _choice(
                values, "BT_REVERSE_PROXY", _REVERSE_PROXIES
            )
            authentik_outpost_url = _exact_origin(
                values.get("BT_AUTHENTIK_OUTPOST_URL", ""),
                "BT_AUTHENTIK_OUTPOST_URL",
            )
            if (
                reverse_proxy == "nginx"
                and urlsplit(authentik_outpost_url).scheme != "http"
            ):
                raise ConfigError(
                    "Nginx requires an internal http Authentik outpost origin; "
                    "use Traefik or Caddy for a verified https upstream"
                )

        return cls(
            identity=identity,
            install_profile=install_profile,
            install_name=install_name,
            ingress_mode=ingress_mode,
            auth_profile=auth_profile,
            public_origin=public_origin,
            cwa_upstream=cwa_upstream,
            cwa_container=cwa_container,
            cwa_network=cwa_network,
            cwa_version=cwa_version,
            compatibility_tier=cwa_tier,
            cwa_identity_header=cwa_identity_header,
            edge_network=edge_network,
            state_dir=state_dir,
            data_dir=data_dir,
            backup_dir=backup_dir,
            unraid_template_dir=unraid_template_dir,
            proxy_port=proxy_port,
            identity_proxy_peer=identity_proxy_peer,
            authentik_version=authentik_version,
            authentik_outpost_url=authentik_outpost_url,
            reverse_proxy=reverse_proxy,
            llm_provider=llm_provider,
            llm_model=llm_model,
            local_url=local_url,
            llm_api_key=llm_api_key,
        )

    @property
    def image(self) -> str:
        return self.identity.image

    def api_environment(self) -> dict[str, str]:
        values = {
            "BT_AUTH_MODE": (
                "cwa_session"
                if self.auth_profile == "cwa-session"
                else "forwarded"
            ),
            "LLM_PROVIDER": self.llm_provider,
            "LLM_MODEL": self.llm_model,
            "BT_LOCAL_URL": self.local_url,
            "LLM_API_KEY": self.llm_api_key,
        }
        if self.install_profile == "compose-existing":
            values["BT_CACHE_OPERATOR_GROUP_ACCESS"] = "true"
        if self.auth_profile == "cwa-session":
            values["BT_CWA_AUTH_URL"] = f"{self.cwa_upstream}/ajax/emailstat"
            values["BT_TRUSTED_PROXY_HOST"] = "translator-proxy"
        else:
            values.update(
                {
                    "BT_IDENTITY_TRUSTED_PROXIES": self.identity_proxy_peer,
                    "BT_TRUSTED_PROXIES": self.identity_proxy_peer,
                    "BT_FORWARDED_SUBJECT_HEADER": "X-authentik-uid",
                    "BT_FORWARDED_ROLES_HEADER": "",
                }
            )
        return values

    def proxy_environment(self) -> dict[str, str]:
        return {
            "BT_PUBLIC_ORIGIN": self.public_origin,
            "CWA_UPSTREAM": self.cwa_upstream,
            "BT_CWA_IDENTITY_HEADER": self.cwa_identity_header,
            "BT_BROWSER_AUTH_MODE": (
                "cwa_session"
                if self.auth_profile == "cwa-session"
                else "forwarded"
            ),
            "BT_BROWSER_CREDENTIALS": (
                "same-origin" if self.auth_profile == "cwa-session" else "include"
            ),
        }

    def public_contract(self) -> dict[str, object]:
        """Return stable non-secret settings suitable for plans and audit logs."""
        return {
            "install_profile": self.install_profile,
            "install_name": self.install_name,
            "ingress_mode": self.ingress_mode,
            "auth_profile": self.auth_profile,
            "public_origin": self.public_origin,
            "cwa_upstream": self.cwa_upstream,
            "cwa_container": self.cwa_container,
            "cwa_network": self.cwa_network,
            "cwa_version": self.cwa_version,
            "compatibility_tier": self.compatibility_tier,
            "cwa_identity_header": self.cwa_identity_header,
            "edge_network": self.edge_network,
            "state_dir": self.state_dir,
            "data_dir": self.data_dir,
            "backup_dir": self.backup_dir,
            "unraid_template_dir": self.unraid_template_dir,
            "proxy_port": self.proxy_port,
            "reverse_proxy": self.reverse_proxy,
            "identity_proxy_peer": self.identity_proxy_peer,
            "authentik_version": self.authentik_version,
            "authentik_outpost_url": self.authentik_outpost_url,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "local_url": self.local_url,
            "has_api_key": bool(self.llm_api_key),
        }


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    version: str
    revision: str
    image: str
    config_fingerprint: str
    install_profile: str
    ingress_mode: str
    auth_profile: str
    cwa_version: str
    compatibility_tier: str
    state_dir: str
    data_dir: str
    backup_dir: str
    resources: dict[str, dict[str, object]]
    api_environment: dict[str, str]
    proxy_environment: dict[str, str]

    @classmethod
    def from_config(cls, config: InstallConfig) -> "DeploymentPlan":
        public = config.public_contract()
        canonical = json.dumps(public, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        proxy_ports = [config.proxy_port] if config.proxy_port is not None else []
        resources: dict[str, dict[str, object]] = {
            "cwa": {
                "name": config.cwa_container,
                "ownership": "external",
            },
            "cwa_network": {
                "name": config.cwa_network,
                "ownership": "external",
            },
            "private_network": {
                "name": f"{config.install_name}-private",
                "ownership": "owned",
            },
            "data": {
                "path": config.data_dir,
                "ownership": "external",
                "retention": "always-preserve",
            },
            "api": {
                "name": f"{config.install_name}-api",
                "ownership": "owned",
                "role": "api",
                "published_ports": [],
            },
            "proxy": {
                "name": f"{config.install_name}-proxy",
                "ownership": "owned",
                "role": "proxy",
                "published_ports": proxy_ports,
            },
        }
        if config.edge_network:
            resources["edge_network"] = {
                "name": config.edge_network,
                "ownership": "external",
            }
        if config.auth_profile == "authentik-forwarded":
            suffix = {
                "nginx": "nginx.conf",
                "traefik": "traefik.yml",
                "caddy": "caddy",
            }[config.reverse_proxy]
            resources["identity_edge_config"] = {
                "path": str(Path(config.state_dir) / f"authentik-edge.{suffix}"),
                "ownership": "owned",
            }
        if config.install_profile == "unraid":
            resources["api_template"] = {
                "path": str(
                    Path(config.unraid_template_dir) / "my-cwa-translate-api.xml"
                ),
                "ownership": "owned",
            }
            resources["proxy_template"] = {
                "path": str(
                    Path(config.unraid_template_dir) / "my-cwa-translate-proxy.xml"
                ),
                "ownership": "owned",
            }
        return cls(
            version=config.identity.version,
            revision=config.identity.sha,
            image=config.image,
            config_fingerprint=fingerprint,
            install_profile=config.install_profile,
            ingress_mode=config.ingress_mode,
            auth_profile=config.auth_profile,
            cwa_version=config.cwa_version,
            compatibility_tier=config.compatibility_tier,
            state_dir=config.state_dir,
            data_dir=config.data_dir,
            backup_dir=config.backup_dir,
            resources=resources,
            api_environment=redact_mapping(config.api_environment()),
            proxy_environment=redact_mapping(config.proxy_environment()),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "version": self.version,
            "revision": self.revision,
            "image": self.image,
            "config_fingerprint": self.config_fingerprint,
            "install_profile": self.install_profile,
            "ingress_mode": self.ingress_mode,
            "auth_profile": self.auth_profile,
            "cwa_version": self.cwa_version,
            "compatibility_tier": self.compatibility_tier,
            "state_dir": self.state_dir,
            "data_dir": self.data_dir,
            "backup_dir": self.backup_dir,
            "resources": self.resources,
            "api_environment": self.api_environment,
            "proxy_environment": self.proxy_environment,
        }


@dataclass(frozen=True, slots=True)
class DeploymentState:
    schema_version: int
    install_id: str
    status: str
    version: str
    revision: str
    image: str
    config_fingerprint: str
    install_profile: str
    auth_profile: str
    resources: dict[str, dict[str, object]]

    @classmethod
    def new(cls, *, install_id: str, plan: DeploymentPlan) -> "DeploymentState":
        try:
            normalized_id = str(uuid.UUID(install_id))
        except (ValueError, AttributeError) as exc:
            raise ConfigError("install_id must be a canonical UUID") from exc
        if not _INSTALL_ID_RE.fullmatch(normalized_id):
            raise ConfigError("install_id must be a canonical UUID")
        return cls(
            schema_version=STATE_SCHEMA_VERSION,
            install_id=normalized_id,
            status="installed",
            version=plan.version,
            revision=plan.revision,
            image=plan.image,
            config_fingerprint=plan.config_fingerprint,
            install_profile=plan.install_profile,
            auth_profile=plan.auth_profile,
            resources=plan.resources,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "install_id": self.install_id,
            "status": self.status,
            "version": self.version,
            "revision": self.revision,
            "image": self.image,
            "config_fingerprint": self.config_fingerprint,
            "install_profile": self.install_profile,
            "auth_profile": self.auth_profile,
            "resources": self.resources,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "DeploymentState":
        if not isinstance(payload, dict):
            raise ConfigError("state must be one JSON object")
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ConfigError("unsupported state schema version")
        expected = {
            "schema_version",
            "install_id",
            "status",
            "version",
            "revision",
            "image",
            "config_fingerprint",
            "install_profile",
            "auth_profile",
            "resources",
        }
        if set(payload) != expected:
            raise ConfigError("state fields do not match the supported schema")
        state = cls(**payload)
        if not _INSTALL_ID_RE.fullmatch(str(state.install_id)):
            raise ConfigError("state contains an invalid install_id")
        if state.status not in {
            "installed",
            "adopted",
            "uninstalling",
            "uninstalled",
            "rolled_back",
        }:
            raise ConfigError("state contains an invalid status")
        if not isinstance(state.resources, dict):
            raise ConfigError("state resources must be an object")
        return state


class StateStore:
    """Atomic private persistence for non-secret deployment ownership state."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "state.json"

    def _validate_destination(self) -> None:
        if self.state_dir.is_symlink() or self.path.is_symlink():
            raise ConfigError("state destination must not be a symbolic link")
        if self.state_dir.exists() and not self.state_dir.is_dir():
            raise ConfigError("state destination must be a directory")

    def save(self, state: DeploymentState) -> None:
        self._validate_destination()
        ensure_directory_durable(self.state_dir)
        payload = json.dumps(state.to_dict(), sort_keys=True, indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".state.json.", dir=self.state_dir
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
            _fsync_directory(self.state_dir)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> DeploymentState:
        try:
            payload = json.loads(
                read_private_text(
                    self.state_dir, self.path.name, label="deployment state"
                )
            )
        except json.JSONDecodeError as exc:
            raise ConfigError("deployment state is not valid JSON") from exc
        return DeploymentState.from_dict(payload)

    def archive(self, state: DeploymentState) -> Path:
        """Preserve one final state before a later install replaces state.json."""
        self._validate_destination()
        if self.load() != state:
            raise ConfigError("deployment state changed before it could be archived")
        if state.status not in {"uninstalled", "rolled_back"}:
            raise ConfigError(
                "only completed uninstall or rollback state can be archived"
            )

        history = self.state_dir / "history"
        if history.is_symlink() or (history.exists() and not history.is_dir()):
            raise ConfigError("state history destination must be a real directory")
        ensure_directory_durable(history)
        target = history / f"{state.install_id}-{state.status}.json"
        if target.is_symlink():
            raise ConfigError("state history entry must not be a symbolic link")
        payload = json.dumps(state.to_dict(), sort_keys=True, indent=2) + "\n"
        if target.exists():
            try:
                if (
                    not target.is_file()
                    or target.stat().st_mode & 0o777 != 0o600
                ):
                    raise ConfigError("state history entry must be a private file")
                if target.read_text(encoding="utf-8") != payload:
                    raise ConfigError("state history entry conflicts with prior evidence")
            except (OSError, UnicodeError) as exc:
                raise ConfigError("state history entry could not be verified") from exc
            return target

        descriptor, temporary_name = tempfile.mkstemp(prefix=".history.", dir=history)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            try:
                os.link(temporary_name, target)
            except FileExistsError:
                try:
                    if (
                        target.is_symlink()
                        or not target.is_file()
                        or target.stat().st_mode & 0o777 != 0o600
                    ):
                        raise ConfigError("state history entry must be a private file")
                    if target.read_text(encoding="utf-8") != payload:
                        raise ConfigError(
                            "state history entry conflicts with prior evidence"
                        )
                except (OSError, UnicodeError) as exc:
                    raise ConfigError(
                        "state history entry could not be verified"
                    ) from exc
            _fsync_directory(history)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        return target


def redact_mapping(values: Mapping[str, str]) -> dict[str, str]:
    """Return a printable copy without any credential-like values."""
    return {
        str(name): "<redacted>" if _SECRET_NAME_RE.search(str(name).upper()) else str(value)
        for name, value in values.items()
    }


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a deliberately small KEY=value format without executing shell code."""
    if not isinstance(text, str):
        raise ConfigError("environment input must be text")
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"line {line_number} must use KEY=value")
        name, raw_value = line.split("=", 1)
        if not _ENV_KEY_RE.fullmatch(name):
            raise ConfigError(f"line {line_number} must use KEY=value")
        if name in parsed:
            raise ConfigError(f"duplicate environment key: {name}")
        if "$(" in raw_value or "${" in raw_value or "`" in raw_value:
            raise ConfigError(f"line {line_number} contains forbidden substitution")
        try:
            tokens = shlex.split(raw_value, comments=False, posix=True)
        except ValueError as exc:
            raise ConfigError(f"line {line_number} contains invalid quoting") from exc
        if len(tokens) > 1:
            raise ConfigError(f"line {line_number} must quote values containing spaces")
        value = tokens[0] if tokens else ""
        parsed[name] = _clean_value(value, name, allow_empty=True)
    return parsed
