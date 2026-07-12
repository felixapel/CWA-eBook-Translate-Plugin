#!/usr/bin/env python3
"""Validate BuildKit SBOM/provenance exports before a release is accepted."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_ATTESTATION_BYTES = 64 * 1024 * 1024
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUILDKIT_V02_BUILD_TYPE = "https://mobyproject.org/buildkit@v1"
BUILDKIT_V1_BUILD_TYPE = (
    "https://github.com/moby/buildkit/blob/master/docs/attestations/"
    "slsa-definitions.md"
)
SOURCE_REPOSITORY_RE = re.compile(
    r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)+\.git$"
)
BASE_IMAGE_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:[._/:+-][a-z0-9]+)*)"
    r":(?P<tag>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)


class AttestationError(ValueError):
    """Expected, sanitized release-policy failure."""


def _load_json(path: Path, label: str) -> Any:
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_ATTESTATION_BYTES:
            raise AttestationError(
                f"{label} must be between 1 and {MAX_ATTESTATION_BYTES} bytes"
            )
        return json.loads(path.read_text(encoding="utf-8"))
    except AttestationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"{label} is not readable bounded JSON") from exc


def _platform_document(payload: Any, platform: str, kind: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AttestationError(f"{kind} root must be an object")
    platform_payload = payload.get(platform)
    if not isinstance(platform_payload, dict):
        raise AttestationError(f"{kind} is missing platform {platform}")
    document = platform_payload.get(kind)
    if not isinstance(document, dict):
        raise AttestationError(f"{kind} document is missing for {platform}")
    return document


def _validate_spdx(payload: Any, platforms: list[str]) -> None:
    for platform in platforms:
        spdx = _platform_document(payload, platform, "SPDX")
        if spdx.get("SPDXID") != "SPDXRef-DOCUMENT":
            raise AttestationError(f"SPDX document identity is invalid for {platform}")
        version = spdx.get("spdxVersion")
        if not isinstance(version, str) or not version.startswith("SPDX-"):
            raise AttestationError(f"SPDX version is invalid for {platform}")
        if spdx.get("dataLicense") != "CC0-1.0":
            raise AttestationError(f"SPDX data license is invalid for {platform}")
        packages = spdx.get("packages")
        if not isinstance(packages, list) or not packages:
            raise AttestationError(f"SPDX packages are empty for {platform}")
        for package in packages:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("SPDXID"), str)
                or not isinstance(package.get("name"), str)
            ):
                raise AttestationError(f"SPDX package entry is invalid for {platform}")


def _valid_builder(builder: Any) -> bool:
    return isinstance(builder, dict) and isinstance(builder.get("id"), str)


def _matches_material(
    material: Any,
    *,
    expected_uri: str,
    digest_algorithm: str,
    expected_digest: str,
    allow_query: bool = False,
) -> bool:
    if not isinstance(material, dict):
        return False
    uri = material.get("uri")
    digest = material.get("digest")
    if not isinstance(uri, str) or not isinstance(digest, dict):
        return False
    if allow_query:
        uri_matches = uri.split("?", 1)[0] == expected_uri
    else:
        uri_matches = uri == expected_uri
    return uri_matches and digest.get(digest_algorithm) == expected_digest


def _validate_config_source(
    config_source: Any,
    *,
    source_uri: str,
    source_sha: str,
    path_field: str,
    platform: str,
) -> None:
    if not isinstance(config_source, dict):
        raise AttestationError(f"provenance config source is missing for {platform}")
    if (
        config_source.get("uri") != source_uri
        or not isinstance(config_source.get("digest"), dict)
        or config_source["digest"].get("sha1") != source_sha
        or config_source.get(path_field) != "Dockerfile"
    ):
        raise AttestationError(f"provenance config source mismatch for {platform}")


def _validate_provenance(
    payload: Any,
    platforms: list[str],
    source_sha: str,
    source_repository: str,
    base_image: str,
    base_digest: str,
) -> None:
    source_uri = f"{source_repository}#{source_sha}"
    base_match = BASE_IMAGE_RE.fullmatch(base_image)
    if base_match is None:
        raise AttestationError("base image must be a normalized tagged image reference")
    base_uri = (
        f"pkg:docker/{base_match.group('name')}@{base_match.group('tag')}"
    )

    for platform in platforms:
        slsa = _platform_document(payload, platform, "SLSA")
        build_definition = slsa.get("buildDefinition")
        if isinstance(build_definition, dict):
            build_type = build_definition.get("buildType")
            dependencies = build_definition.get("resolvedDependencies")
            external_parameters = build_definition.get("externalParameters")
            config_source = (
                external_parameters.get("configSource")
                if isinstance(external_parameters, dict)
                else None
            )
            run_details = slsa.get("runDetails")
            builder = run_details.get("builder") if isinstance(run_details, dict) else None
            path_field = "path"
        else:
            build_type = slsa.get("buildType")
            dependencies = slsa.get("materials")
            builder = slsa.get("builder")
            invocation = slsa.get("invocation")
            config_source = (
                invocation.get("configSource") if isinstance(invocation, dict) else None
            )
            path_field = "entryPoint"

        expected_build_type = (
            BUILDKIT_V1_BUILD_TYPE
            if isinstance(build_definition, dict)
            else BUILDKIT_V02_BUILD_TYPE
        )
        if build_type != expected_build_type:
            raise AttestationError(f"unexpected provenance build type for {platform}")
        if not _valid_builder(builder):
            raise AttestationError(f"provenance builder is missing for {platform}")
        if not isinstance(dependencies, list) or not dependencies:
            raise AttestationError(f"provenance materials are missing for {platform}")

        _validate_config_source(
            config_source,
            source_uri=source_uri,
            source_sha=source_sha,
            path_field=path_field,
            platform=platform,
        )
        if not any(
            _matches_material(
                dependency,
                expected_uri=source_uri,
                digest_algorithm="sha1",
                expected_digest=source_sha,
            )
            for dependency in dependencies
        ):
            raise AttestationError(f"provenance source material mismatch for {platform}")
        if not any(
            _matches_material(
                dependency,
                expected_uri=base_uri,
                digest_algorithm="sha256",
                expected_digest=base_digest,
                allow_query=True,
            )
            for dependency in dependencies
        ):
            raise AttestationError(f"provenance base digest mismatch for {platform}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate multi-platform SPDX and SLSA release attestations"
    )
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--base-digest", required=True)
    parser.add_argument("--platform", action="append", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not SHA1_RE.fullmatch(args.source_sha):
            raise AttestationError("source SHA must be 40 lowercase hexadecimal characters")
        if not SOURCE_REPOSITORY_RE.fullmatch(args.source_repository):
            raise AttestationError("source repository must be a normalized HTTPS Git URL")
        if not SHA256_RE.fullmatch(args.base_digest):
            raise AttestationError("base digest must be 64 lowercase hexadecimal characters")
        platforms = list(dict.fromkeys(args.platform))
        if len(platforms) != len(args.platform):
            raise AttestationError("release platforms must be unique")

        sbom = _load_json(args.sbom, "SBOM")
        provenance = _load_json(args.provenance, "provenance")
        _validate_spdx(sbom, platforms)
        _validate_provenance(
            provenance,
            platforms,
            args.source_sha,
            args.source_repository,
            args.base_image,
            args.base_digest,
        )
    except AttestationError as exc:
        print(f"release attestation verification failed: {exc}", file=sys.stderr)
        return 2

    print("release attestations: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
