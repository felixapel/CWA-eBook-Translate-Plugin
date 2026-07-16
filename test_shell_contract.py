"""Fail-closed contracts for operator-facing shell helpers."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class ShellContractTests(unittest.TestCase):
    def test_bootstrap_files_are_forced_to_linux_line_endings(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("btctl text eol=lf", attributes)
        self.assertIn("Dockerfile.btctl text eol=lf", attributes)

    def test_operator_helpers_are_executable(self):
        for name in (
            "btctl",
            "install_unraid.sh",
        ):
            self.assertTrue((ROOT / name).stat().st_mode & 0o111, name)

    def test_bash_helpers_enable_strict_mode(self):
        for name in (
            "btctl",
            "install_unraid.sh",
        ):
            source = (ROOT / name).read_text()
            self.assertIn("set -euo pipefail", source, name)

    def test_btctl_dispatches_to_python_or_a_hardened_local_operator(self):
        source = (ROOT / "btctl").read_text()
        self.assertEqual(source.splitlines()[0], "#!/usr/bin/env bash")
        for contract in (
            "command -v python3",
            "git --version",
            '"$SCRIPT_DIR/btctl.py"',
            "Dockerfile.btctl",
            "--target",
            "source-exporter",
            "--network",
            "none",
            "--read-only",
            "--pids-limit",
            "--cap-drop",
            "ALL",
            "no-new-privileges:true",
            "/var/run/docker.sock",
            "BTCTL_EXPECTED_REVISION",
            "core.fsmonitor",
            "GIT_NO_REPLACE_OBJECTS=1",
            "source_exporter_dockerfile",
            "DAC_READ_SEARCH",
            "BTCTL_LOCK_DIRECTORY",
            "lock:ro",
            "DOCKER_HOST=unix:///var/run/docker.sock",
            "[ -S /var/run/docker.sock ]",
            "/run/cwa-translate-btctl-locks",
            '[ "$second" = "$HOST_LOCK_DIRECTORY" ]',
        ):
            self.assertIn(contract, source)
        self.assertNotRegex(source, r"(?m)^\s*(?:source|eval)\s")
        self.assertNotIn("image rm --force", source)
        self.assertNotIn(
            'cp -- "$SCRIPT_DIR/Dockerfile.btctl"',
            source,
        )

    def test_python_without_git_selects_the_container_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            (tools / "dirname").symlink_to("/usr/bin/dirname")
            python = tools / "python3"
            python.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = -c ]; then exit 0; fi\n"
                "echo unexpected-native-path >&2\n"
                "exit 42\n",
                encoding="utf-8",
            )
            python.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(ROOT / "btctl"), "--help"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": str(tools),
                    "BTCTL_DOCKER_BIN": "missing-docker-for-contract",
                },
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(completed.returncode, 42)
        self.assertNotIn("unexpected-native-path", completed.stderr)
        self.assertRegex(
            completed.stderr,
            r"containerized Unraid path must run as root|Docker is required",
        )

    def test_native_dispatch_clears_container_only_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            (tools / "dirname").symlink_to("/usr/bin/dirname")
            (tools / "git").symlink_to("/usr/bin/git")
            python = tools / "python3"
            python.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = -c ]; then exit 0; fi\n"
                "[ -z \"${BTCTL_EXPECTED_REVISION:-}\" ] || exit 41\n"
                "[ -z \"${BTCTL_OPERATOR_REVISION:-}\" ] || exit 41\n"
                "[ -z \"${BTCTL_LOCK_DIRECTORY:-}\" ] || exit 41\n"
                "exit 0\n",
                encoding="utf-8",
            )
            python.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(ROOT / "btctl"), "--help"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": str(tools),
                    "BTCTL_EXPECTED_REVISION": "a" * 40,
                    "BTCTL_OPERATOR_REVISION": "b" * 40,
                    "BTCTL_LOCK_DIRECTORY": "/tmp/untrusted-lock",
                },
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_retired_personal_unraid_helpers_are_absent(self):
        for name in ("deploy_unraid.sh", "verify_unraid.sh"):
            self.assertFalse((ROOT / name).exists(), name)

    def test_install_is_a_root_only_btctl_wrapper(self):
        source = (ROOT / "install_unraid.sh").read_text()
        self.assertIn('if [ "$#" -ne 1 ]', source)
        self.assertIn('"${EUID:-$(id -u)}" -ne 0', source)
        self.assertIn('"$SCRIPT_DIR/btctl" --repository "$SCRIPT_DIR" plan', source)
        self.assertIn('install --env "$ENV_FILE" --yes', source)
        self.assertNotRegex(source, r"\bread\s+-r(?:\s+-p)?\s+")
        self.assertNotIn("CWA_PATH", source)
        self.assertNotIn("overlay/read.html", source)
        self.assertNotIn("docker build", source)
        self.assertNotIn("docker pull", source)
        self.assertNotIn("ghcr.io", source)

    def test_legacy_unraid_template_is_hard_gated_to_v214_migration(self):
        template = (ROOT / "my-book-translator-api.xml.tmpl").read_text()
        self.assertIn("2.1.4", template)
        self.assertIn("LEGACY ONLY", template)
        self.assertIn("migration", template)
        self.assertNotIn("latest", template)
        self.assertNotIn('Type="Port"', template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
