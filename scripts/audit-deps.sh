#!/usr/bin/env bash
# Dependency audit against the same complete locks enforced by CI.
#
# Exit code: 0 = clean, non-zero = vulnerabilities found.
#
# Usage: ./scripts/audit-deps.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Python (pip-audit on requirements.txt)"
if command -v pip-audit >/dev/null 2>&1; then
    pip-audit -r requirements.txt --strict --disable-pip --no-deps
else
    echo "pip-audit not installed; install requirements-audit.txt with --require-hashes"
    exit 2
fi

echo
echo "==> Frontend (npm audit on package.json + package-lock.json)"
npm audit --audit-level=high
