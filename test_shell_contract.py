"""Fail-closed contracts for operator-facing shell helpers."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class ShellContractTests(unittest.TestCase):
    def test_operator_helpers_are_executable(self):
        for name in ("deploy_unraid.sh", "verify_unraid.sh", "install_unraid.sh"):
            self.assertTrue((ROOT / name).stat().st_mode & 0o111, name)

    def test_bash_helpers_enable_strict_mode(self):
        for name in ("deploy_unraid.sh", "verify_unraid.sh", "install_unraid.sh"):
            source = (ROOT / name).read_text()
            self.assertIn("set -euo pipefail", source, name)

    def test_remote_target_is_one_quoted_value(self):
        for name in ("deploy_unraid.sh", "verify_unraid.sh"):
            source = (ROOT / name).read_text()
            self.assertIn('REMOTE="${UNRAID_USER}@${UNRAID_HOST}"', source, name)
            self.assertNotRegex(source, r"ssh\s+\$UNRAID_USER@\$UNRAID_HOST")
            self.assertNotRegex(source, r"scp\s+[^\n]*\$UNRAID_USER@\$UNRAID_HOST")

    def test_deploy_passes_remote_values_as_arguments(self):
        source = (ROOT / "deploy_unraid.sh").read_text()
        self.assertIn("bash -s --", source)
        self.assertIn("<<'REMOTE_SCRIPT'", source)
        for unsafe_fragment in (
            '"cd $API_DIR',
            "http://${LLM_HOST}",
            '"mkdir -p $CWA_OVERLAY_DIR',
        ):
            self.assertNotIn(unsafe_fragment, source)

    def test_verify_fails_when_frontend_hash_does_not_match(self):
        source = (ROOT / "verify_unraid.sh").read_text()
        mismatch = re.search(
            r'else\s+echo "ERROR: Frontend hash mismatch!"(?P<body>.*?)fi',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(mismatch)
        self.assertRegex(mismatch.group("body"), r"\bexit\s+1\b")

    def test_install_reads_paths_verbatim(self):
        source = (ROOT / "install_unraid.sh").read_text()
        self.assertRegex(source, r"\bread\s+-r(?:\s+-p)?\s+")
        self.assertIn('mkdir -p -- "$CWA_PATH/overlay"', source)
        self.assertIn(
            'cp -- "$SCRIPT_DIR/overlay/read.html" '
            '"$CWA_PATH/overlay/read.html"',
            source,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
