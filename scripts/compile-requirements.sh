#!/usr/bin/env bash
# Regenerate reviewed Python locks from public PyPI with a fixed compiler.
set -euo pipefail

cd "$(dirname "$0")/.."

EXPECTED_PYTHON="3.11"
EXPECTED_PIP_COMPILE="7.5.3"
LOCK_PYTHON="${LOCK_PYTHON:-python3.11}"
actual_python="$("$LOCK_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$actual_python" != "$EXPECTED_PYTHON" ]; then
    echo "expected Python $EXPECTED_PYTHON, got: $actual_python" >&2
    exit 2
fi
actual_compiler="$("$LOCK_PYTHON" -c \
    'import importlib.metadata; print(importlib.metadata.version("pip-tools"))')"
if [ "$actual_compiler" != "$EXPECTED_PIP_COMPILE" ]; then
    echo "expected pip-compile $EXPECTED_PIP_COMPILE, got: $actual_compiler" >&2
    exit 2
fi

common=(
    --no-config
    --generate-hashes
    --strip-extras
    --resolver=backtracking
    --no-emit-index-url
    --no-emit-trusted-host
    --no-header
)

PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple \
    "$LOCK_PYTHON" -m piptools compile "${common[@]}" \
    --output-file=requirements.txt requirements.in
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple \
    "$LOCK_PYTHON" -m piptools compile "${common[@]}" --allow-unsafe \
    --output-file=requirements-audit.txt requirements-audit.in
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple \
    "$LOCK_PYTHON" -m piptools compile "${common[@]}" --allow-unsafe \
    --output-file=requirements-compile.txt requirements-compile.in
