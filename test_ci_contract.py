"""Static contracts preventing required CI gates from degrading to skipped."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKER_NAMES = ROOT / "scripts" / "ci-docker-names.sh"


class CIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = CI.read_text()

    def test_all_backend_contract_suites_are_required(self):
        for command in (
            "python3 test_translation.py",
            "python3 test_hardening.py",
            "python3 -m unittest -v test_work_budget test_provider_budget test_cache_v2 test_context_cache test_singleflight test_ci_contract test_release_contract test_supply_chain_contract test_shell_contract test_container_contract test_cleanup_token test_api_schema test_error_privacy",
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
        self.assertRegex(
            self.workflow,
            r"(?m)^  docker-smoke:\n    runs-on: weebdb-docker$",
        )
        self.assertRegex(self.workflow, r"(?m)^\s*run: docker version\s*$")
        self.assertRegex(self.workflow, r"(?m)^\s*run: docker build ")

    def test_docker_smoke_exercises_the_published_proxy_path(self):
        self.assertIn("./scripts/container-smoke.sh", self.workflow)
        self.assertIn("sh scripts/ci-docker-names.sh", self.workflow)
        self.assertNotIn("bt-smoke-${{ github.run_id }}", self.workflow)
        self.assertNotIn(
            "bt-audit:${{ github.run_id }}-${{ github.run_attempt }}",
            self.workflow,
        )
        self.assertIn('docker build -t "$SMOKE_IMAGE" .', self.workflow)
        self.assertIn('./scripts/container-smoke.sh "$SMOKE_IMAGE" "$SMOKE_PREFIX"', self.workflow)
        self.assertNotIn("docker build -t bt-audit:ci", self.workflow)

    def test_docker_names_are_isolated_across_repositories(self):
        def derive(repository, run_id="4242", run_attempt="1"):
            with tempfile.TemporaryDirectory() as temp_dir:
                env_file = Path(temp_dir) / "github-env"
                env = os.environ.copy()
                env.update({
                    "GITHUB_REPOSITORY": repository,
                    "GITHUB_RUN_ID": run_id,
                    "GITHUB_RUN_ATTEMPT": run_attempt,
                    "GITHUB_ENV": str(env_file),
                })
                subprocess.run(
                    ["sh", str(DOCKER_NAMES)],
                    cwd=ROOT,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return dict(
                    line.split("=", 1)
                    for line in env_file.read_text().splitlines()
                )

        first = derive("felix/CWA-translate-plugin")
        second = derive("another-owner/another-repository")
        repeated = derive("felix/CWA-translate-plugin")
        next_attempt = derive("felix/CWA-translate-plugin", run_attempt="2")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first["SMOKE_PREFIX"], second["SMOKE_PREFIX"])
        self.assertNotEqual(first["SMOKE_IMAGE"], second["SMOKE_IMAGE"])
        self.assertNotEqual(
            first["SMOKE_PREFIX"], next_attempt["SMOKE_PREFIX"])
        self.assertRegex(
            first["SMOKE_PREFIX"], r"^bt-ci-[0-9a-f]{20}$")
        self.assertRegex(
            first["SMOKE_IMAGE"], r"^bt-audit:[0-9a-f]{20}$")

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
