import unittest
from pathlib import Path

from scripts.check_docs import REPOSITORY, collect_errors


class DocumentationContractTests(unittest.TestCase):
    def test_repository_documentation_contract(self):
        self.assertEqual(collect_errors(REPOSITORY), [])

    def test_checker_uses_repository_version_as_release_truth(self):
        version = (Path(REPOSITORY) / "VERSION").read_text(encoding="utf-8").strip()
        readme = (Path(REPOSITORY) / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"git switch --detach v{version}", readme)

    def test_public_readme_leads_with_the_safe_managed_path(self):
        readme = (Path(REPOSITORY) / "README.md").read_text(encoding="utf-8")
        for contract in (
            "./btctl plan --env",
            "./btctl install --env",
            "./btctl doctor --env",
            "full checkout including `.git`",
            "does not require host Python, host Git or NerdTools",
            "docs/install/btctl.md",
            "docs/reference/compatibility.md",
        ):
            self.assertIn(contract, readme)
        for unsupported_claim in (
            "Zero-touch install",
            "never truncates real translations",
            "does not publish container images",
            "Gemma's pre-training coverage",
        ):
            self.assertNotIn(unsupported_claim, readme)

    def test_managed_guides_cover_install_and_complete_lifecycle(self):
        root = Path(REPOSITORY)
        install = (root / "docs/install/btctl.md").read_text(encoding="utf-8")
        lifecycle = (root / "docs/operations/lifecycle.md").read_text(
            encoding="utf-8"
        )
        for command in ("plan", "install", "doctor"):
            self.assertIn(f"./btctl {command} --env", install)
        for command in ("doctor", "adopt", "upgrade", "rollback", "uninstall"):
            self.assertIn(f"./btctl {command} --env", lifecycle)
        for contract in (
            "host Python, host Git and NerdTools are not required",
            "Docker socket is equivalent to root",
            "warm ordinary build cache",
            "full Git checkout",
        ):
            self.assertIn(contract, install)

    def test_ca_guide_keeps_the_combined_api_private_and_digest_pinned(self):
        source = (
            Path(REPOSITORY) / "docs/install/community-applications.md"
        ).read_text(encoding="utf-8")
        for contract in (
            "Unraid 7.3.2",
            "CWA 4.0.6",
            "linux/amd64",
            "BT_ROLE=all",
            "101:102",
            "0700",
            "8080",
            "8385",
            "8390",
            "not published",
            "sha256",
            "BT_AUTH_MODE=cwa_session",
            "/ajax/emailstat",
        ):
            self.assertIn(contract, source)
        self.assertRegex(source, r"Allow Reverse Proxy\s+Authentication")
        self.assertNotIn(":latest", source)

    def test_authentik_guide_is_fail_closed_and_edge_owned(self):
        source = (Path(REPOSITORY) / "docs/install/authentik.md").read_text(
            encoding="utf-8"
        )
        for contract in (
            "BT_AUTH_PROFILE=authentik-forwarded",
            "BT_INGRESS_MODE=docker-edge",
            "BT_IDENTITY_PROXY_IP=",
            "BT_AUTHENTIK_OUTPOST_URL=",
            "./btctl auth-snippet --env",
            "./btctl doctor --env",
            "X-authentik-uid",
            "Cookie",
            "CVE-2026-25748",
            "2026.2.5+",
            "2026.5.4+",
        ):
            self.assertIn(contract, source)
        self.assertNotIn("BT_AUTH_MODE=disabled", source)
        self.assertNotIn("BT_ALLOW_INSECURE_AUTH=true", source)

    def test_compatibility_and_troubleshooting_are_actionable(self):
        root = Path(REPOSITORY)
        compatibility = (root / "docs/reference/compatibility.md").read_text(
            encoding="utf-8"
        )
        for contract in (
            "CWA 4.x",
            "CWA 3.1.4",
            "Unraid",
            "Compose",
            "Chromium",
            "Nginx",
            "Traefik",
            "Caddy",
            "OpenAI-compatible",
            "vLLM",
            "Ollama",
            "LM Studio",
            "llama.cpp",
        ):
            self.assertIn(contract, compatibility)

        troubleshooting = (
            root / "docs/operations/troubleshooting.md"
        ).read_text(encoding="utf-8")
        for contract in (
            "./btctl doctor --env",
            "Community Applications install has no `btctl` state",
            "no mapping for 8390",
            "/bt-config.json",
            "authentik-forwarded",
            "BT_IDENTITY_PROXY_IP",
        ):
            self.assertIn(contract, troubleshooting)

    def test_public_support_files_are_actionable(self):
        root = Path(REPOSITORY)
        security = (root / "SECURITY.md").read_text(encoding="utf-8")
        contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
        bug = (root / ".github/ISSUE_TEMPLATE/bug_report.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("felixguillermoapel@gmail.com", security)
        self.assertIn("GitHub Security Advisories", security)
        for command in ("npm ci", "npm test", "npm run test:e2e", "python3"):
            self.assertIn(command, contributing)
        self.assertIn("./btctl doctor", bug)
        self.assertIn("exact tag or commit", bug)


if __name__ == "__main__":
    unittest.main()
