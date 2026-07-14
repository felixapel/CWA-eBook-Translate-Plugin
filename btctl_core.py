"""Pure configuration contracts shared by the source-only installer and tests."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import shlex
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
_SECRET_NAME_RE = re.compile(r"(?:KEY|PASSWORD|SECRET|TOKEN)$")
_INSTALL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_INSTALL_PROFILES = frozenset({"unraid", "compose-existing", "compose-bundled"})
_INGRESS_MODES = frozenset({"published", "docker-edge"})
_AUTH_PROFILES = frozenset({"cwa-session", "authentik-forwarded"})
_REVERSE_PROXIES = frozenset({"nginx", "traefik", "caddy"})
STATE_SCHEMA_VERSION = 1


class ConfigError(ValueError):
    """An installation value is ambiguous, unsafe, or incomplete."""


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


def _exact_origin(value: object, name: str) -> str:
    cleaned = _clean_value(value, name)
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{name} must be an exact http(s) origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in parsed.hostname)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigError(f"{name} must be an exact http(s) origin")
    return cleaned.rstrip("/")


def _http_url(value: object, name: str) -> str:
    cleaned = _clean_value(value, name)
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{name} must be an absolute http(s) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in parsed.hostname)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigError(f"{name} must be an absolute http(s) URL")
    return cleaned


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


def _absolute_dir(value: object, name: str) -> str:
    cleaned = _clean_value(value, name)
    path = Path(cleaned)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise ConfigError(f"{name} must be an absolute non-root directory")
    return str(path)


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
    if year == 2025 and minor in {10, 12} and patch < 4:
        raise ConfigError(
            "BT_AUTHENTIK_VERSION is affected by CVE-2026-25748; "
            "use 2025.10.4, 2025.12.4, or a later maintained release"
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
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if Path(top_level).resolve() != root.resolve():
            raise ConfigError("repository must be the Git checkout root")
        subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "VERSION"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
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
    ingress_mode: str
    auth_profile: str
    public_origin: str
    cwa_upstream: str
    cwa_container: str
    cwa_network: str
    cwa_version: str
    compatibility_tier: str
    edge_network: str
    state_dir: str
    data_dir: str
    backup_dir: str
    proxy_port: int | None
    identity_proxy_peer: str
    authentik_version: str
    reverse_proxy: str
    llm_provider: str
    llm_model: str
    local_url: str
    llm_api_key: str

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, str], identity: ReleaseIdentity
    ) -> "InstallConfig":
        install_profile = _choice(
            values, "BT_INSTALL_PROFILE", _INSTALL_PROFILES
        )
        ingress_mode = _choice(values, "BT_INGRESS_MODE", _INGRESS_MODES)
        auth_profile = _choice(values, "BT_AUTH_PROFILE", _AUTH_PROFILES)
        public_origin = _exact_origin(values.get("BT_PUBLIC_ORIGIN", ""), "BT_PUBLIC_ORIGIN")
        cwa_upstream = _exact_origin(values.get("CWA_UPSTREAM", ""), "CWA_UPSTREAM")

        cwa_container = _clean_value(
            values.get("BT_CWA_CONTAINER", ""), "BT_CWA_CONTAINER"
        )
        if not _CONTAINER_RE.fullmatch(cwa_container):
            raise ConfigError("BT_CWA_CONTAINER must be one exact Docker container name")
        cwa_network = _clean_value(
            values.get("BT_CWA_NETWORK", ""), "BT_CWA_NETWORK"
        )
        if not _NETWORK_RE.fullmatch(cwa_network):
            raise ConfigError("BT_CWA_NETWORK must be one exact Docker network name")
        cwa_version = _clean_value(
            values.get("BT_CWA_VERSION", ""), "BT_CWA_VERSION"
        )
        cwa_tier = compatibility_tier(cwa_version)
        state_dir = _absolute_dir(values.get("BT_STATE_DIR", ""), "BT_STATE_DIR")
        data_dir = _absolute_dir(values.get("BT_DATA_DIR", ""), "BT_DATA_DIR")
        backup_dir = _absolute_dir(values.get("BT_BACKUP_DIR", ""), "BT_BACKUP_DIR")
        if len({state_dir, data_dir, backup_dir}) != 3:
            raise ConfigError("BT_STATE_DIR, BT_DATA_DIR, and BT_BACKUP_DIR must differ")

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

        llm_provider = _clean_value(values.get("LLM_PROVIDER", ""), "LLM_PROVIDER")
        llm_model = _clean_value(values.get("LLM_MODEL", ""), "LLM_MODEL")
        if not _TOKEN_RE.fullmatch(llm_provider):
            raise ConfigError("LLM_PROVIDER contains unsupported characters")
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
        elif not llm_api_key or local_url:
            raise ConfigError(
                "cloud providers require LLM_API_KEY and forbid BT_LOCAL_URL"
            )

        identity_proxy_peer = ""
        authentik_version = ""
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

        return cls(
            identity=identity,
            install_profile=install_profile,
            ingress_mode=ingress_mode,
            auth_profile=auth_profile,
            public_origin=public_origin,
            cwa_upstream=cwa_upstream,
            cwa_container=cwa_container,
            cwa_network=cwa_network,
            cwa_version=cwa_version,
            compatibility_tier=cwa_tier,
            edge_network=edge_network,
            state_dir=state_dir,
            data_dir=data_dir,
            backup_dir=backup_dir,
            proxy_port=proxy_port,
            identity_proxy_peer=identity_proxy_peer,
            authentik_version=authentik_version,
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
        if self.auth_profile == "cwa-session":
            values["BT_CWA_AUTH_URL"] = f"{self.cwa_upstream}/ajax/emailstat"
        else:
            values.update(
                {
                    "BT_IDENTITY_TRUSTED_PROXIES": self.identity_proxy_peer,
                    "BT_FORWARDED_SUBJECT_HEADER": "X-authentik-uid",
                    "BT_FORWARDED_ROLES_HEADER": "",
                }
            )
        return values

    def proxy_environment(self) -> dict[str, str]:
        return {
            "BT_PUBLIC_ORIGIN": self.public_origin,
            "CWA_UPSTREAM": self.cwa_upstream,
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
            "ingress_mode": self.ingress_mode,
            "auth_profile": self.auth_profile,
            "public_origin": self.public_origin,
            "cwa_upstream": self.cwa_upstream,
            "cwa_container": self.cwa_container,
            "cwa_network": self.cwa_network,
            "cwa_version": self.cwa_version,
            "compatibility_tier": self.compatibility_tier,
            "edge_network": self.edge_network,
            "state_dir": self.state_dir,
            "data_dir": self.data_dir,
            "backup_dir": self.backup_dir,
            "proxy_port": self.proxy_port,
            "reverse_proxy": self.reverse_proxy,
            "identity_proxy_peer": self.identity_proxy_peer,
            "authentik_version": self.authentik_version,
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
        suffix = fingerprint[:12]
        cwa_ownership = (
            "owned" if config.install_profile == "compose-bundled" else "external"
        )
        proxy_ports = [config.proxy_port] if config.proxy_port is not None else []
        resources: dict[str, dict[str, object]] = {
            "cwa": {
                "name": config.cwa_container,
                "ownership": cwa_ownership,
            },
            "cwa_network": {
                "name": config.cwa_network,
                "ownership": "external",
            },
            "private_network": {
                "name": f"cwa-translate-{suffix}-private",
                "ownership": "owned",
            },
            "data": {
                "path": config.data_dir,
                "ownership": "external",
                "retention": "always-preserve",
            },
            "api": {
                "name": f"cwa-translate-{suffix}-api",
                "ownership": "owned",
                "role": "api",
                "published_ports": [],
            },
            "proxy": {
                "name": f"cwa-translate-{suffix}-proxy",
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
        if state.status not in {"installed", "adopted", "uninstalled"}:
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
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
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
            directory_fd = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> DeploymentState:
        self._validate_destination()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError("deployment state does not exist") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("deployment state is not valid JSON") from exc
        return DeploymentState.from_dict(payload)


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
