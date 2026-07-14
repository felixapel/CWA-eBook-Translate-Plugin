"""Fail-closed authentication boundaries for the translation API.

The module deliberately produces only opaque subjects. Raw tokens, forwarded
identities, and CWA cookies must never enter cache keys, metrics, or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Callable, Mapping
from urllib.parse import urlsplit

import requests

from singleflight import (
    SingleFlight,
    SingleFlightCapacityError,
    SingleFlightTimeout,
)


log = logging.getLogger("book-translator.auth")

_SUPPORTED_MODES = frozenset({"token", "cwa_session", "forwarded", "disabled"})
_COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,63}$")


class AuthConfigError(ValueError):
    """Authentication configuration is unsafe or incomplete."""


class AuthRejected(RuntimeError):
    """The request did not establish an authenticated identity."""


class AuthUnavailable(RuntimeError):
    """Authentication could not be decided because its authority is down."""


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    subject: str
    roles: frozenset[str]
    mode: str


def _opaque_subject(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8", errors="strict")).hexdigest()
    return f"{prefix}:{digest}"


def _header_value(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is not None:
        return str(value)
    wanted = name.casefold()
    for candidate, candidate_value in headers.items():
        if str(candidate).casefold() == wanted:
            return str(candidate_value)
    return ""


def _finite_positive(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AuthConfigError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise AuthConfigError(f"{name} must be a finite positive number")
    return parsed


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise AuthConfigError(f"{name} must be a positive integer")
    try:
        parsed = int(str(value), 10)
    except (TypeError, ValueError) as exc:
        raise AuthConfigError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise AuthConfigError(f"{name} must be a positive integer")
    return parsed


def _is_safe_token(value: str) -> bool:
    return bool(value) and len(value) <= 4096 and all(
        33 <= ord(character) <= 126 for character in value
    )


def _validate_http_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AuthConfigError("BT_CWA_AUTH_URL must be a clean absolute URL")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise AuthConfigError("BT_CWA_AUTH_URL must be an http(s) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.path != "/ajax/emailstat"
        or any(character.isspace() for character in (parsed.hostname or ""))
    ):
        raise AuthConfigError(
            "BT_CWA_AUTH_URL must target the exact /ajax/emailstat http(s) endpoint"
        )
    return value


class _ValidationCache:
    """Small TTL/LRU cache containing only opaque session hashes and booleans."""

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, bool]] = OrderedDict()

    def get(self, key: str) -> bool | None:
        now = time.monotonic()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expires_at, valid = item
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return valid

    def put(self, key: str, valid: bool) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, valid)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class RequestAuthenticator:
    """Authenticate one request and return an opaque, server-owned identity."""

    def __init__(
        self,
        *,
        mode: str,
        api_token: str = "",
        cwa_auth_url: str = "",
        cwa_cookie_names: tuple[str, ...] = ("session", "remember_token"),
        identity_trusted_proxies: tuple[str, ...] = (),
        forwarded_subject_header: str = "X-BT-Subject",
        forwarded_roles_header: str = "X-BT-Roles",
        cwa_timeout_seconds: float = 2.0,
        cwa_cache_ttl_seconds: float = 15.0,
        cwa_cache_max_entries: int = 10_000,
        cwa_max_inflight: int = 8,
        cwa_max_response_bytes: int = 262_144,
        http_get: Callable[..., object] | None = None,
    ) -> None:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in _SUPPORTED_MODES:
            raise AuthConfigError(
                f"BT_AUTH_MODE must be one of {sorted(_SUPPORTED_MODES)}"
            )
        self.mode = normalized_mode

        if api_token and not _is_safe_token(api_token):
            raise AuthConfigError(
                "BT_API_TOKEN must be a printable ASCII token without spaces"
            )
        if self.mode == "token" and not api_token:
            raise AuthConfigError("BT_API_TOKEN is required in token mode")
        self._api_token = api_token

        self._cwa_auth_url = ""
        self._cookie_names: tuple[str, ...] = ()
        self._cwa_timeout_seconds = _finite_positive(
            cwa_timeout_seconds, "BT_CWA_AUTH_TIMEOUT_SECONDS"
        )
        cache_ttl = _finite_positive(
            cwa_cache_ttl_seconds, "BT_CWA_AUTH_CACHE_TTL_SECONDS"
        )
        cache_max = _positive_int(
            cwa_cache_max_entries, "BT_CWA_AUTH_CACHE_MAX_ENTRIES"
        )
        max_inflight = _positive_int(cwa_max_inflight, "BT_CWA_AUTH_MAX_INFLIGHT")
        self._cwa_max_response_bytes = _positive_int(
            cwa_max_response_bytes, "BT_CWA_AUTH_MAX_RESPONSE_BYTES"
        )
        self._validation_cache = _ValidationCache(
            ttl_seconds=cache_ttl, max_entries=cache_max
        )
        self._validation_flights = SingleFlight(
            max_entries=max_inflight, result_ttl_seconds=0
        )
        # Tests may inject a deterministic transport. Production creates a
        # fresh trust_env=False Session per probe so HTTP(S)_PROXY cannot
        # receive CWA cookies and response cookies cannot bleed between users.
        self._http_get = http_get

        if self.mode == "cwa_session":
            if not cwa_auth_url:
                raise AuthConfigError("BT_CWA_AUTH_URL is required in cwa_session mode")
            self._cwa_auth_url = _validate_http_url(cwa_auth_url)
            names = tuple(dict.fromkeys(cwa_cookie_names))
            if not names or len(names) > 16 or any(
                not isinstance(name, str) or not _COOKIE_NAME_RE.fullmatch(name)
                for name in names
            ):
                raise AuthConfigError(
                    "BT_CWA_AUTH_COOKIE_NAMES must contain 1-16 safe cookie names"
                )
            self._cookie_names = names

        try:
            self._identity_proxy_networks = tuple(
                ipaddress.ip_network(value, strict=False)
                for value in identity_trusted_proxies
            )
        except ValueError as exc:
            raise AuthConfigError(
                "BT_IDENTITY_TRUSTED_PROXIES contains an invalid IP or CIDR"
            ) from exc
        if self.mode == "forwarded" and not self._identity_proxy_networks:
            raise AuthConfigError(
                "BT_IDENTITY_TRUSTED_PROXIES is required in forwarded mode"
            )
        if self.mode == "forwarded" and any(
            network.prefixlen != network.max_prefixlen
            for network in self._identity_proxy_networks
        ):
            raise AuthConfigError(
                "BT_IDENTITY_TRUSTED_PROXIES must contain only exact /32 or /128 peers"
            )

        if not isinstance(forwarded_subject_header, str) or not _HEADER_NAME_RE.fullmatch(
            forwarded_subject_header
        ):
            raise AuthConfigError(
                "BT_FORWARDED_SUBJECT_HEADER must be a bounded HTTP header name"
            )
        if not isinstance(forwarded_roles_header, str) or (
            forwarded_roles_header
            and not _HEADER_NAME_RE.fullmatch(forwarded_roles_header)
        ):
            raise AuthConfigError(
                "BT_FORWARDED_ROLES_HEADER must be empty or a bounded HTTP header name"
            )
        self._forwarded_subject_header = forwarded_subject_header
        self._forwarded_roles_header = forwarded_roles_header

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        http_get: Callable[..., object] | None = None,
    ) -> "RequestAuthenticator":
        source = env if env is not None else os.environ
        mode = source.get("BT_AUTH_MODE", "token")
        if (
            str(mode).strip().lower() == "disabled"
            and source.get("BT_ALLOW_INSECURE_AUTH", "false").strip().lower()
            not in {"1", "true", "yes"}
        ):
            raise AuthConfigError(
                "BT_AUTH_MODE=disabled requires BT_ALLOW_INSECURE_AUTH=true"
            )
        cookie_names = tuple(
            value.strip()
            for value in source.get(
                "BT_CWA_AUTH_COOKIE_NAMES", "session,remember_token"
            ).split(",")
            if value.strip()
        )
        trusted_proxies = tuple(
            value.strip()
            for value in source.get("BT_IDENTITY_TRUSTED_PROXIES", "").split(",")
            if value.strip()
        )
        authenticator = cls(
            mode=mode,
            api_token=source.get("BT_API_TOKEN", ""),
            cwa_auth_url=source.get("BT_CWA_AUTH_URL", ""),
            cwa_cookie_names=cookie_names,
            identity_trusted_proxies=trusted_proxies,
            forwarded_subject_header=source.get(
                "BT_FORWARDED_SUBJECT_HEADER", "X-BT-Subject"
            ),
            forwarded_roles_header=source.get(
                "BT_FORWARDED_ROLES_HEADER", "X-BT-Roles"
            ),
            cwa_timeout_seconds=source.get("BT_CWA_AUTH_TIMEOUT_SECONDS", "2"),
            cwa_cache_ttl_seconds=source.get("BT_CWA_AUTH_CACHE_TTL_SECONDS", "15"),
            cwa_cache_max_entries=source.get("BT_CWA_AUTH_CACHE_MAX_ENTRIES", "10000"),
            cwa_max_inflight=source.get("BT_CWA_AUTH_MAX_INFLIGHT", "8"),
            cwa_max_response_bytes=source.get(
                "BT_CWA_AUTH_MAX_RESPONSE_BYTES", "262144"
            ),
            http_get=http_get,
        )
        if authenticator.mode == "disabled":
            log.warning(
                "BT_AUTH_MODE=disabled: all requests share one anonymous tenant; development only"
            )
        return authenticator

    @property
    def cache_entries(self) -> int:
        return len(self._validation_cache)

    def authenticate(
        self, headers: Mapping[str, str], remote_addr: str | None
    ) -> AuthIdentity:
        if self.mode == "disabled":
            return AuthIdentity("legacy-anonymous", frozenset(), self.mode)
        if self.mode == "token":
            return self._authenticate_token(headers)
        if self.mode == "forwarded":
            return self._authenticate_forwarded(headers, remote_addr)
        return self._authenticate_cwa_session(headers)

    def _authenticate_token(self, headers: Mapping[str, str]) -> AuthIdentity:
        provided = _header_value(headers, "X-BT-Token")
        if not _is_safe_token(provided) or not hmac.compare_digest(
            provided, self._api_token
        ):
            raise AuthRejected("authentication rejected")
        return AuthIdentity(
            _opaque_subject("token", self._api_token),
            frozenset({"operator"}),
            self.mode,
        )

    def _authenticate_forwarded(
        self, headers: Mapping[str, str], remote_addr: str | None
    ) -> AuthIdentity:
        # The browser cookie belongs only at the identity edge. Its presence
        # here proves that the edge did not apply the required sanitization.
        if _header_value(headers, "Cookie"):
            raise AuthRejected("authentication rejected")
        try:
            peer = ipaddress.ip_address(remote_addr or "")
        except ValueError:
            raise AuthRejected("authentication rejected") from None
        if not any(peer in network for network in self._identity_proxy_networks):
            raise AuthRejected("authentication rejected")

        subject = _header_value(headers, self._forwarded_subject_header)
        if (
            not subject
            or subject != subject.strip()
            or len(subject) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in subject)
        ):
            raise AuthRejected("authentication rejected")

        roles_header = (
            _header_value(headers, self._forwarded_roles_header)
            if self._forwarded_roles_header
            else ""
        )
        if len(roles_header) > 2048:
            raise AuthRejected("authentication rejected")
        roles = tuple(
            role.strip() for role in roles_header.split(",") if role.strip()
        )
        if len(roles) > 32 or any(not _ROLE_RE.fullmatch(role) for role in roles):
            raise AuthRejected("authentication rejected")
        return AuthIdentity(
            _opaque_subject("forwarded", subject),
            frozenset(roles),
            self.mode,
        )

    def _selected_cookies(self, headers: Mapping[str, str]) -> tuple[str, str]:
        raw_cookie = _header_value(headers, "Cookie")
        if (
            not raw_cookie
            or len(raw_cookie) > 8192
            or any(ord(character) < 32 or ord(character) == 127 for character in raw_cookie)
        ):
            raise AuthRejected("authentication rejected")
        parsed: SimpleCookie[str] = SimpleCookie()
        try:
            parsed.load(raw_cookie)
        except CookieError:
            raise AuthRejected("authentication rejected") from None
        selected = [
            (name, parsed[name].coded_value)
            for name in self._cookie_names
            if name in parsed and parsed[name].coded_value
        ]
        if not selected:
            raise AuthRejected("authentication rejected")
        cookie_header = "; ".join(f"{name}={value}" for name, value in selected)
        canonical = "\0".join(f"{name}={value}" for name, value in selected)
        return cookie_header, canonical

    def _authenticate_cwa_session(
        self, headers: Mapping[str, str]
    ) -> AuthIdentity:
        cookie_header, canonical = self._selected_cookies(headers)
        session_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        valid = self._validation_cache.get(session_key)
        if valid is None:
            try:
                result = self._validation_flights.run(
                    session_key,
                    lambda: self._probe_cwa(cookie_header),
                    timeout=self._cwa_timeout_seconds + 0.25,
                )
            except (SingleFlightCapacityError, SingleFlightTimeout):
                raise AuthUnavailable("authentication authority unavailable") from None
            valid = result.value
            self._validation_cache.put(session_key, valid)
        if not valid:
            raise AuthRejected("authentication rejected")
        return AuthIdentity(
            f"cwa-session:{session_key}",
            frozenset(),
            self.mode,
        )

    def _probe_cwa(self, cookie_header: str) -> bool:
        response = None
        session = None
        deadline = time.monotonic() + self._cwa_timeout_seconds
        try:
            http_get = self._http_get
            if http_get is None:
                session = requests.Session()
                session.trust_env = False
                http_get = session.get
            response = http_get(
                self._cwa_auth_url,
                headers={"Accept": "application/json", "Cookie": cookie_header},
                timeout=self._cwa_timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
            if time.monotonic() >= deadline:
                raise AuthUnavailable("authentication authority unavailable")
            status = int(response.status_code)
            content_type = str(response.headers.get("Content-Type", ""))
            if status == 200:
                if content_type.split(";", 1)[0].strip().lower() != "application/json":
                    return False
                content_length = str(response.headers.get("Content-Length", "")).strip()
                if content_length:
                    try:
                        declared_length = int(content_length, 10)
                    except ValueError:
                        raise AuthUnavailable(
                            "authentication authority unavailable"
                        ) from None
                    if (
                        declared_length < 0
                        or declared_length > self._cwa_max_response_bytes
                    ):
                        raise AuthUnavailable("authentication authority unavailable")

                payload = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    # requests' read timeout is an inactivity timeout. Enforce
                    # the operator's budget across the complete streamed body
                    # so a peer cannot retain a validation slot by dripping
                    # one chunk just before every socket timeout.
                    if time.monotonic() >= deadline:
                        raise AuthUnavailable("authentication authority unavailable")
                    if not chunk:
                        continue
                    if not isinstance(chunk, bytes):
                        raise AuthUnavailable("authentication authority unavailable")
                    if len(payload) + len(chunk) > self._cwa_max_response_bytes:
                        raise AuthUnavailable("authentication authority unavailable")
                    payload.extend(chunk)
                if time.monotonic() >= deadline:
                    raise AuthUnavailable("authentication authority unavailable")
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return False
                # CWA's authenticated /ajax/emailstat contract is a JSON list
                # of task objects (an empty list is valid). Checking that
                # minimal shape prevents an unrelated public JSON endpoint
                # from being accepted as an identity authority.
                return isinstance(parsed, list) and all(
                    isinstance(item, dict) for item in parsed
                )
            if status in {401, 403} or 300 <= status < 400:
                return False
            raise AuthUnavailable("authentication authority unavailable")
        except AuthUnavailable:
            raise
        except Exception as exc:
            log.warning("CWA authentication probe failed type=%s", type(exc).__name__)
            raise AuthUnavailable("authentication authority unavailable") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
