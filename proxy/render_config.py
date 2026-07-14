"""Validate proxy inputs and atomically render the nginx configuration."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


EX_CONFIG = 78
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$"
)
_SIZE_RE = re.compile(r"^[1-9][0-9]{0,9}[kKmMgG]?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_PLACEHOLDER_RE = re.compile(r"\$\{(?:BT_|CWA_)[A-Z0-9_]*\}")


class ProxyConfigError(ValueError):
    """A proxy environment value cannot safely enter nginx configuration."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise ProxyConfigError(f"{name} must be a clean non-empty ASCII value")
    return value


def _validated_base_url(env: Mapping[str, str], name: str) -> tuple[str, str, str]:
    value = _required(env, name)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ProxyConfigError(f"{name} must be an exact http(s) origin") from exc

    hostname = parsed.hostname or ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ProxyConfigError(f"{name} must be an exact http(s) origin")

    try:
        ipaddress.ip_address(hostname)
        is_ipv6 = ":" in hostname
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(hostname):
            raise ProxyConfigError(f"{name} contains an invalid hostname")
        is_ipv6 = False

    if port is not None and not 1 <= port <= 65535:
        raise ProxyConfigError(f"{name} contains an invalid port")
    normalized_host = f"[{hostname}]" if is_ipv6 else hostname
    if port is not None:
        normalized_host += f":{port}"

    # Reject ambiguous spellings such as a dangling colon instead of silently
    # turning them into a different authority.
    if parsed.netloc.casefold() != normalized_host.casefold():
        raise ProxyConfigError(f"{name} must contain only a host and optional port")

    scheme = parsed.scheme.lower()
    return f"{scheme}://{normalized_host}", scheme, normalized_host


def _validated_port(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if not value.isdecimal():
        raise ProxyConfigError(f"{name} must be an integer from 1 to 65535")
    port = int(value, 10)
    if not 1 <= port <= 65535:
        raise ProxyConfigError(f"{name} must be an integer from 1 to 65535")
    return str(port)


def _validated_size(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if not _SIZE_RE.fullmatch(value):
        raise ProxyConfigError(f"{name} must be a finite positive nginx size")
    return value.lower()


def _validated_version(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if not _VERSION_RE.fullmatch(value):
        raise ProxyConfigError(f"{name} must be a bounded version token")
    return value


def _validated_header_name(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if not _HEADER_NAME_RE.fullmatch(value):
        raise ProxyConfigError(f"{name} must be a bounded HTTP header name")
    return value


def _validated_browser_config(env: Mapping[str, str]) -> dict[str, str]:
    auth_mode = _required(env, "BT_BROWSER_AUTH_MODE")
    credentials = _required(env, "BT_BROWSER_CREDENTIALS")
    supported = {
        "cwa_session": "same-origin",
        "forwarded": "include",
    }
    if auth_mode not in supported or supported[auth_mode] != credentials:
        raise ProxyConfigError(
            "BT_BROWSER_AUTH_MODE and BT_BROWSER_CREDENTIALS are not a supported pair"
        )
    return {
        "apiUrl": "/bt-api",
        "authMode": auth_mode,
        "credentials": credentials,
    }


def _atomic_write(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def render(template_path: Path, output_path: Path, env: Mapping[str, str]) -> None:
    cwa_upstream, _, _ = _validated_base_url(env, "CWA_UPSTREAM")
    api_upstream, _, _ = _validated_base_url(env, "BT_API_UPSTREAM")
    _, public_scheme, public_host = _validated_base_url(env, "BT_PUBLIC_ORIGIN")
    replacements = {
        "${CWA_UPSTREAM}": cwa_upstream,
        "${BT_API_UPSTREAM}": api_upstream,
        "${BT_PROXY_PORT}": _validated_port(env, "BT_PROXY_PORT"),
        "${BT_UI_VERSION}": _validated_version(env, "BT_UI_VERSION"),
        "${BT_CWA_IDENTITY_HEADER}": _validated_header_name(
            env, "BT_CWA_IDENTITY_HEADER"
        ),
        "${BT_PUBLIC_SCHEME}": public_scheme,
        "${BT_PUBLIC_HOST}": public_host,
        "${BT_CWA_MAX_BODY_SIZE}": _validated_size(
            env, "BT_CWA_MAX_BODY_SIZE"
        ),
    }

    template = template_path.read_text(encoding="utf-8")
    rendered = template
    for placeholder, value in replacements.items():
        if placeholder not in rendered:
            raise ProxyConfigError(f"proxy template is missing {placeholder}")
        rendered = rendered.replace(placeholder, value)
    unresolved = _PLACEHOLDER_RE.search(rendered)
    if unresolved:
        raise ProxyConfigError("proxy template contains an unresolved placeholder")

    _atomic_write(output_path, rendered)


def render_browser_config(output_path: Path, config: Mapping[str, str]) -> None:
    _atomic_write(
        output_path,
        json.dumps(dict(config), sort_keys=True, separators=(",", ":")) + "\n",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: render_config.py TEMPLATE NGINX_OUTPUT BROWSER_CONFIG_OUTPUT",
            file=sys.stderr,
        )
        return 64
    try:
        browser_config = _validated_browser_config(os.environ)
        render(Path(argv[1]), Path(argv[2]), os.environ)
        render_browser_config(Path(argv[3]), browser_config)
    except (ProxyConfigError, OSError) as exc:
        # Never print environment values: upstream URLs may contain private
        # names, and rejected credential-bearing URLs must not reach logs.
        if isinstance(exc, ProxyConfigError):
            detail = str(exc)
        else:
            detail = "proxy configuration could not be written"
        print(f"[proxy-config] ERROR: {detail}", file=sys.stderr)
        return EX_CONFIG
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
