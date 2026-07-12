"""Fail-closed contracts for immutable third-party build inputs."""
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".gitea" / "workflows" / "ci.yml",
    ROOT / ".gitea" / "workflows" / "release.yml",
)

PINNED_ACTIONS = {
    "actions/checkout": (
        "34e114876b0b11c390a56381ad16ebd13914f8d5",
        "v4.3.1",
    ),
    "actions/setup-node": (
        "49933ea5288caeca8642d1e84afbd3f7d6820020",
        "v4.4.0",
    ),
    "docker/setup-qemu-action": (
        "c7c53464625b32c7a7e944ae62b3e17d2b600130",
        "v3.7.0",
    ),
    "docker/setup-buildx-action": (
        "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
        "v3.12.0",
    ),
    "docker/build-push-action": (
        "10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
        "v6.19.2",
    ),
    "sigstore/cosign-installer": (
        "6f9f17788090df1f26f669e9d70d6ae9567deba6",
        "v4.1.2",
    ),
}

BASE_IMAGE = (
    "python:3.11-alpine@"
    "sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4"
)
NODE_VERSION = "24.18.0"
SBOM_GENERATOR = (
    "docker.io/docker/buildkit-syft-scanner@"
    "sha256:79e7b013cbec16bbb436f312819a49a4a57752b2270c1a9332ae1a10fcc82a68"
)
APK_PACKAGES = {
    "libgomp": "15.2.0-r5",
    "libxml2": "2.13.9-r2",
    "nginx": "1.30.3-r0",
    "pcre2": "10.47-r1",
}

USES_LINE = re.compile(
    r"(?m)^\s*-?\s*uses:\s*"
    r"(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@"
    r"(?P<revision>[0-9a-f]{40})\s+#\s+(?P<version>v\d+(?:\.\d+){0,2})\s*$"
)


class SupplyChainContractTests(unittest.TestCase):
    def test_every_external_action_is_pinned_to_the_reviewed_commit(self):
        for workflow in WORKFLOWS:
            source = workflow.read_text()
            uses_lines = [line for line in source.splitlines() if "uses:" in line]
            matches = list(USES_LINE.finditer(source))
            self.assertEqual(
                len(matches),
                len(uses_lines),
                f"{workflow}: every uses line needs a 40-hex commit and version comment",
            )
            for match in matches:
                action = match.group("action")
                self.assertIn(action, PINNED_ACTIONS, f"{workflow}: unreviewed action {action}")
                self.assertEqual(
                    (match.group("revision"), match.group("version")),
                    PINNED_ACTIONS[action],
                    f"{workflow}: unexpected pin for {action}",
                )

    def test_gitea_and_github_ci_remain_byte_identical(self):
        github_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_bytes()
        gitea_ci = (ROOT / ".gitea" / "workflows" / "ci.yml").read_bytes()
        self.assertEqual(gitea_ci, github_ci)

    def test_supply_chain_contract_is_a_required_backend_gate(self):
        for workflow in WORKFLOWS:
            self.assertIn(
                "test_supply_chain_contract",
                workflow.read_text(),
                f"{workflow}: supply-chain assertions must run in CI",
            )

    def test_container_base_and_operating_system_packages_are_immutable(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertEqual(dockerfile.splitlines()[0], f"FROM {BASE_IMAGE}")
        self.assertEqual(dockerfile.count("apk add"), 1)
        flattened = dockerfile.replace("\\\n", " ")
        apk_add = re.search(r"RUN apk add --no-cache\s+([^\n]+)", flattened)
        self.assertIsNotNone(apk_add)
        installed = dict(token.split("=", 1) for token in apk_add.group(1).split())
        self.assertEqual(installed, APK_PACKAGES)

    def test_python_installs_require_the_reviewed_hashes_and_wheels(self):
        expected_install = (
            "python3 -m pip install --break-system-packages "
            "--require-hashes --only-binary=:all: -r requirements.txt"
        )
        expected_tools = (
            "python3 -m pip install --break-system-packages "
            "--require-hashes --only-binary=:all: -r requirements-audit.txt"
        )
        for workflow in WORKFLOWS:
            source = workflow.read_text()
            self.assertIn(expected_install, source)
            self.assertIn(expected_tools, source)
            self.assertIn(
                "python3 -m pip_audit -r requirements.txt "
                "--strict --disable-pip --no-deps",
                source,
            )
            self.assertNotIn("piptools compile", source)
            self.assertNotIn("requirements-pinned.txt", source)

        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("COPY requirements.txt .", dockerfile)
        self.assertIn(
            "pip install --no-cache-dir --require-hashes "
            "--only-binary=:all: -r requirements.txt",
            dockerfile,
        )

    def test_python_lock_files_pin_and_hash_every_dependency(self):
        for name in (
            "requirements.txt",
            "requirements-audit.txt",
            "requirements-compile.txt",
        ):
            lock = (ROOT / name).read_text()
            logical_lock = lock.replace("\\\n", " ")
            requirements = re.findall(
                r"(?m)^([a-z0-9][a-z0-9_.-]*)==([^\s]+)([^\n]*)",
                logical_lock,
            )
            self.assertGreater(len(requirements), 2, f"{name}: lock is unexpectedly small")
            for package, _version, options in requirements:
                self.assertIn(
                    "--hash=sha256:", options, f"{name}: {package} has no sha256 hash"
                )
            self.assertNotRegex(lock, r"(?m)^[a-z0-9_.-]+\s*(?:[<>~!]=?|===)")
            self.assertNotIn("--extra-index-url", lock)
            self.assertNotIn("--trusted-host", lock)
            self.assertNotRegex(lock, r"(?m)^[a-z0-9_.-]+\s*@\s*")

    def test_direct_runtime_imports_are_declared_as_direct_dependencies(self):
        intent = (ROOT / "requirements.in").read_text()
        self.assertIn("requests>=2.31.0,<3.0", intent)
        self.assertIn("urllib3>=2.0,<3.0", intent)

    def test_lock_regeneration_is_pinned_and_uses_public_pypi(self):
        compiler = (ROOT / "scripts" / "compile-requirements.sh").read_text()
        self.assertIn('EXPECTED_PYTHON="3.11"', compiler)
        self.assertIn('EXPECTED_PIP_COMPILE="7.5.3"', compiler)
        self.assertIn("PIP_CONFIG_FILE=/dev/null", compiler)
        self.assertIn("PIP_INDEX_URL=https://pypi.org/simple", compiler)
        for option in (
            "--generate-hashes",
            "--resolver=backtracking",
            "--no-emit-index-url",
            "--no-emit-trusted-host",
        ):
            self.assertIn(option, compiler)
        self.assertIn("requirements.in", compiler)
        self.assertIn("requirements-audit.in", compiler)
        self.assertIn("requirements-compile.in", compiler)

    def test_local_dependency_audit_uses_the_same_complete_locks(self):
        audit_path = ROOT / "scripts" / "audit-deps.sh"
        audit = audit_path.read_text()
        self.assertIn(
            "pip-audit -r requirements.txt --strict --disable-pip --no-deps",
            audit,
        )
        self.assertIn("npm audit --audit-level=high", audit)
        self.assertNotIn("--omit=dev", audit)
        self.assertTrue(audit_path.stat().st_mode & 0o111)

        compiler_path = ROOT / "scripts" / "compile-requirements.sh"
        self.assertTrue(compiler_path.stat().st_mode & 0o111)

    def test_npm_lock_has_integrity_for_every_registry_artifact(self):
        lock = json.loads((ROOT / "package-lock.json").read_text())
        self.assertGreaterEqual(lock["lockfileVersion"], 3)
        for path, package in lock["packages"].items():
            if not path or package.get("link"):
                continue
            if package.get("resolved", "").startswith("https://registry.npmjs.org/"):
                self.assertRegex(
                    package.get("integrity", ""),
                    r"^sha512-[A-Za-z0-9+/]+={0,2}$",
                    f"{path}: registry artifact lacks a sha512 integrity pin",
                )

    def test_frontend_ci_uses_one_exact_supported_node_release(self):
        self.assertEqual((ROOT / ".node-version").read_text().strip(), NODE_VERSION)
        for workflow in WORKFLOWS:
            source = workflow.read_text()
            self.assertIn('node-version-file: ".node-version"', source)
            self.assertNotRegex(source, r"(?m)^\s*node-version:\s*")

    def test_release_sbom_generator_is_digest_pinned(self):
        release = (ROOT / ".gitea" / "workflows" / "release.yml").read_text()
        self.assertIn(f"sbom: generator={SBOM_GENERATOR}", release)
        self.assertNotRegex(release, r"(?m)^\s+sbom:\s+true\s*$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
