#!/usr/bin/env python3
"""Verify that a release tag is safe to publish from the Gitea authority.

This command is intentionally read-only. It validates local repository state
and queries the public GitHub mirror with ``git ls-remote``; it never creates,
fetches, pushes, or rewrites a ref.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


CORE = r"(?:0|[1-9][0-9]*)"
PRERELEASE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
TAG_RE = re.compile(
    rf"^v(?P<version>{CORE}\.{CORE}\.{CORE}(?:-{PRERELEASE_ID}(?:\.{PRERELEASE_ID})*)?)$"
)
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REF_RE = re.compile(r"^refs/(?:heads|remotes)/[0-9A-Za-z._/-]+$")
UI_VERSION_RE = re.compile(
    r"\b(?:const|let|var)\s+BT_UI_VERSION\s*=\s*(['\"])([^'\"]+)\1"
)
OVERLAY_CSS_VERSION_RE = re.compile(
    r"translator\.css[^?\r\n]*\?v=([0-9A-Za-z.-]+)"
)
OVERLAY_JS_VERSION_RE = re.compile(
    r"translator\.js[^?\r\n]*\?v=([0-9A-Za-z.-]+)"
)
CHANGELOG_VERSION_RE = re.compile(
    r"(?m)^## \[(?!Unreleased\])([^\]]+)\](?: - [0-9]{4}-[0-9]{2}-[0-9]{2})?\s*$"
)


class PreflightError(Exception):
    """A stable, operator-actionable release rejection."""


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreflightError(f"cannot read {label}") from exc


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(_read_text(path, label))
    except json.JSONDecodeError as exc:
        raise PreflightError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must contain a JSON object")
    return value


def _git(
    repository: Path,
    args: list[str],
    *,
    label: str,
    accepted_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"{label} could not be checked") from exc
    if result.returncode not in accepted_codes:
        raise PreflightError(f"{label} could not be verified")
    return result


def _git_value(repository: Path, args: list[str], *, label: str) -> str:
    value = _git(repository, args, label=label).stdout.strip()
    if not value:
        raise PreflightError(f"{label} returned no value")
    return value


def _validate_main_ref(repository: Path, main_ref: str) -> str:
    if not REF_RE.fullmatch(main_ref) or any(
        fragment in main_ref for fragment in ("..", "//", "@{")
    ):
        raise PreflightError("main ref is not a safe full branch ref")
    check = _git(
        repository,
        ["check-ref-format", main_ref],
        label="main ref syntax",
        accepted_codes=(0, 1),
    )
    if check.returncode != 0:
        raise PreflightError("main ref is not a valid Git ref")
    return _git_value(
        repository,
        ["rev-parse", "--verify", f"{main_ref}^{{commit}}"],
        label="main ref",
    )


def _version_surfaces(repository: Path) -> dict[str, str]:
    package = _read_json(repository / "package.json", "package.json")
    lock = _read_json(repository / "package-lock.json", "package-lock.json")
    lock_packages = lock.get("packages")
    if not isinstance(lock_packages, dict) or not isinstance(lock_packages.get(""), dict):
        raise PreflightError("package-lock.json has no root package metadata")

    ui_matches = UI_VERSION_RE.findall(
        _read_text(repository / "static" / "translator.js", "static/translator.js")
    )
    if len(ui_matches) != 1:
        raise PreflightError("ui version marker must appear exactly once")

    overlay = _read_text(repository / "overlay" / "read.html", "overlay/read.html")
    overlay_css_matches = OVERLAY_CSS_VERSION_RE.findall(overlay)
    overlay_js_matches = OVERLAY_JS_VERSION_RE.findall(overlay)
    if len(overlay_css_matches) != 1:
        raise PreflightError("overlay_css version marker must appear exactly once")
    if len(overlay_js_matches) != 1:
        raise PreflightError("overlay_js version marker must appear exactly once")

    changelog_matches = CHANGELOG_VERSION_RE.findall(
        _read_text(repository / "CHANGELOG.md", "CHANGELOG.md")
    )
    if not changelog_matches:
        raise PreflightError("changelog has no released version entry")

    return {
        "version_file": _read_text(repository / "VERSION", "VERSION").strip(),
        "package": package.get("version"),
        "lock": lock.get("version"),
        "lock_root": lock_packages[""].get("version"),
        "ui": ui_matches[0][1],
        "overlay_css": overlay_css_matches[0],
        "overlay_js": overlay_js_matches[0],
        "changelog": changelog_matches[0],
    }


def _verify_versions(repository: Path, version: str) -> None:
    for surface, actual in _version_surfaces(repository).items():
        if not isinstance(actual, str) or actual != version:
            raise PreflightError(
                f"{surface} version does not match release version {version}"
            )


def _verify_mirror(
    repository: Path,
    mirror_url: str,
    tag: str,
    local_tag_object: str,
    expected_commit: str,
) -> str:
    if not mirror_url or any(char in mirror_url for char in ("\n", "\r", "\0")):
        raise PreflightError("mirror URL is invalid")
    tag_ref = f"refs/tags/{tag}"
    result = _git(
        repository,
        ["ls-remote", "--exit-code", "--", mirror_url, tag_ref, f"{tag_ref}^{{}}"],
        label="mirror tag",
    )
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2 and SHA_RE.fullmatch(fields[0]):
            refs[fields[1]] = fields[0]

    mirror_object = refs.get(tag_ref)
    mirror_commit = refs.get(f"{tag_ref}^{{}}")
    if mirror_object is None or mirror_commit is None:
        raise PreflightError("mirror tag must be the same annotated tag")
    if mirror_commit != expected_commit:
        raise PreflightError("mirror tag commit does not match the release SHA")
    if mirror_object != local_tag_object:
        raise PreflightError("mirror tag object does not match the local annotated tag")
    return mirror_commit


def verify_release(
    repository: Path,
    tag: str,
    expected_sha: str,
    main_ref: str,
    mirror_url: str,
) -> dict[str, str]:
    match = TAG_RE.fullmatch(tag)
    if match is None or len(tag) > 128:
        raise PreflightError(
            "tag is not a valid SemVer release tag (vMAJOR.MINOR.PATCH[-PRERELEASE])"
        )
    if SHA_RE.fullmatch(expected_sha) is None:
        raise PreflightError("release SHA must be a lowercase Git object ID")

    repository = repository.resolve()
    if not repository.is_dir():
        raise PreflightError("repository path is not a directory")
    version = match.group("version")
    _verify_versions(repository, version)

    head_sha = _git_value(
        repository,
        ["rev-parse", "--verify", "HEAD^{commit}"],
        label="checked-out commit",
    )
    if head_sha != expected_sha:
        raise PreflightError("checked-out commit does not match the release SHA")

    tag_ref = f"refs/tags/{tag}"
    tag_type = _git_value(
        repository,
        ["cat-file", "-t", tag_ref],
        label="local release tag",
    )
    if tag_type != "tag":
        raise PreflightError("local release ref must be an annotated tag")
    local_tag_object = _git_value(
        repository,
        ["rev-parse", "--verify", tag_ref],
        label="local annotated tag object",
    )
    tag_sha = _git_value(
        repository,
        ["rev-parse", "--verify", f"{tag_ref}^{{commit}}"],
        label="local release tag commit",
    )
    if tag_sha != expected_sha:
        raise PreflightError("local release tag does not point at the release SHA")

    main_sha = _validate_main_ref(repository, main_ref)
    ancestry = _git(
        repository,
        ["merge-base", "--is-ancestor", expected_sha, main_sha],
        label="release ancestry",
        accepted_codes=(0, 1),
    )
    if ancestry.returncode != 0:
        raise PreflightError(f"release SHA is not reachable from {main_ref}")

    mirror_sha = _verify_mirror(
        repository, mirror_url, tag, local_tag_object, expected_sha
    )
    return {
        "main_sha": main_sha,
        "mirror_sha": mirror_sha,
        "sha": expected_sha,
        "tag": tag,
        "tag_object": local_tag_object,
        "version": version,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--main-ref", default="refs/remotes/origin/main")
    parser.add_argument("--mirror-url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_release(
            args.repository, args.tag, args.sha, args.main_ref, args.mirror_url
        )
    except PreflightError as exc:
        print(f"release preflight rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
