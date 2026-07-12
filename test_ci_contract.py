"""Static contracts preventing required CI gates from degrading to skipped."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


class CIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = CI.read_text()

    def test_all_backend_contract_suites_are_required(self):
        for command in (
            "python3 test_translation.py",
            "python3 test_hardening.py",
            "python3 -m unittest -v test_work_budget test_provider_budget test_ci_contract test_cleanup_token",
        ):
            self.assertIn(command, self.workflow)

    def test_node_install_and_audit_include_locked_dev_tree(self):
        self.assertRegex(self.workflow, r"(?m)^\s*- run: npm ci\s*$")
        self.assertNotRegex(self.workflow, r"(?m)^\s*- run: npm install\s*$")
        self.assertRegex(
            self.workflow, r"(?m)^\s*- run: npm audit --audit-level=high\s*$")
        self.assertNotIn("npm audit --omit=dev", self.workflow)

    def test_docker_gate_cannot_report_success_when_docker_is_missing(self):
        self.assertNotIn("Detect Docker", self.workflow)
        self.assertNotIn("docker.outputs.available", self.workflow)
        self.assertNotIn("skipping docker-smoke", self.workflow)
        self.assertRegex(self.workflow, r"(?m)^\s*run: docker version\s*$")
        self.assertRegex(self.workflow, r"(?m)^\s*run: docker build ")

    def test_package_lock_root_metadata_matches_package_manifest(self):
        package = json.loads((ROOT / "package.json").read_text())
        lock = json.loads((ROOT / "package-lock.json").read_text())
        root = lock["packages"][""]
        for key in ("name", "version", "license"):
            self.assertEqual(lock.get(key, root.get(key)), package[key])
            self.assertEqual(root[key], package[key])

    def test_required_steps_have_no_continue_on_error(self):
        self.assertNotIn("continue-on-error", self.workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
