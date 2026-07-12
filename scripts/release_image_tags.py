#!/usr/bin/env python3
"""Render deterministic OCI image tags for one validated release tag."""
from __future__ import annotations

import argparse
import re
import sys

from release_preflight import TAG_RE


IMAGE_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)


class ImageTagError(Exception):
    """A safe release-image tag rejection."""


def render_tags(tag: str, images: list[str]) -> list[str]:
    match = TAG_RE.fullmatch(tag)
    if match is None or len(tag) > 128:
        raise ImageTagError("invalid registry-safe SemVer release tag")
    if not images:
        raise ImageTagError("at least one image repository is required")

    version = match.group("version")
    stable = "-" not in version
    major, minor, _patch = version.split(".", 2)
    aliases = [version]
    if stable:
        aliases.extend((f"{major}.{minor}", "latest"))

    rendered: list[str] = []
    seen: set[str] = set()
    for image in images:
        if (
            IMAGE_RE.fullmatch(image) is None
            or ".." in image
            or "//" in image
            or image in seen
        ):
            raise ImageTagError(f"invalid or duplicate image repository: {image!r}")
        seen.add(image)
        rendered.extend(f"{image}:{alias}" for alias in aliases)
    return rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--image", action="append", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        tags = render_tags(args.tag, args.image)
    except ImageTagError as exc:
        print(f"release image tags rejected: {exc}", file=sys.stderr)
        return 2
    print("\n".join(tags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
