#!/usr/bin/env bash
# Dependency audit — runs `pip-audit` against requirements.txt and
# `npm audit` against package.json + lockfile. Used by CI; can be run
# locally before pushing.
#
# Exit code: 0 = clean, non-zero = vulnerabilities found.
#
# Usage: ./scripts/audit-deps.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Python (pip-audit on requirements.txt)"
if command -v pip-audit >/dev/null 2>&1; then
    pip-audit -r requirements.txt --strict
else
    echo "pip-audit not installed; install with: pip install pip-audit"
    exit 2
fi

echo
echo "==> Frontend (npm audit on package.json + package-lock.json)"
if [ -f package-lock.json ]; then
    npm audit --omit=dev
else
    echo "no package-lock.json; skipping"
fi
