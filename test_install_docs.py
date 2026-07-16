"""Contracts keeping the supported install and recovery documentation executable."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parent
README = ROOT / "README.md"
UNRAID = ROOT / "docs" / "DEPLOY_UNRAID.md"
UNRAID_CA = ROOT / "docs" / "DEPLOY_UNRAID_CA.md"
COMPOSE = ROOT / "docs" / "DEPLOY_COMPOSE.md"
AUTHENTIK = ROOT / "docs" / "AUTHENTIK.md"
COMPATIBILITY = ROOT / "docs" / "COMPATIBILITY.md"
TROUBLESHOOTING = ROOT / "docs" / "TROUBLESHOOTING.md"
PRODUCTION = ROOT / "docs" / "PRODUCTION_READINESS.md"
ARCHITECTURE = ROOT / "docs" / "ARCHITECTURE.md"
SECURITY = ROOT / "SECURITY.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
CODE_OF_CONDUCT = ROOT / "CODE_OF_CONDUCT.md"
BUG_TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
FEATURE_TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.md"


class InstallDocumentationContractTests(unittest.TestCase):
    def test_readme_leads_with_managed_checkout_install_and_verification(self):
        readme = README.read_text(encoding="utf-8")
        installation = readme.split("## 🚀 Installation", 1)[1].split(
            "## ⚡ Performance", 1
        )[0]

        self.assertIn("./btctl plan --env", installation)
        self.assertIn("./btctl install --env", installation)
        self.assertIn("./btctl doctor --env", installation)
        self.assertIn("docs/DEPLOY_UNRAID.md", installation)
        self.assertIn("docs/DEPLOY_COMPOSE.md", installation)
        self.assertIn("docs/COMPATIBILITY.md", installation)
        self.assertNotIn("git clone --branch v2.2.0", readme)
        self.assertNotIn("docker compose up -d --build", installation)

    def test_public_readme_uses_real_release_coordinates_and_bounded_claims(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn(
            "https://github.com/felixapel/CWA-eBook-Translate-Plugin.git",
            readme,
        )
        self.assertIn("git switch --detach v2.2.1", readme)
        for stale in (
            "<repository-url>",
            "<release-tag-or-full-reviewed-commit>",
            "Zero-touch install",
            "never truncates real translations",
            "does not publish container images",
        ):
            self.assertNotIn(stale, readme)

    def test_public_support_and_community_files_are_actionable(self):
        security = SECURITY.read_text(encoding="utf-8")
        conduct = CODE_OF_CONDUCT.read_text(encoding="utf-8")
        contributing = CONTRIBUTING.read_text(encoding="utf-8")
        bug = BUG_TEMPLATE.read_text(encoding="utf-8")
        feature = FEATURE_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("felixguillermoapel@gmail.com", security)
        self.assertIn("GitHub Security Advisories", security)
        for heading in ("## Scope", "## Enforcement", "## Attribution"):
            self.assertIn(heading, conduct)
        self.assertIn("felixguillermoapel@gmail.com", conduct)
        for command in (
            "npm ci",
            "npm test",
            "npm run test:e2e",
            "python3 -m unittest",
        ):
            self.assertIn(command, contributing)
        self.assertNotIn("Create a `.env` file", contributing)
        self.assertNotIn("latest docker image", bug)
        self.assertIn("./btctl doctor", bug)
        self.assertIn("exact tag or commit", bug)
        self.assertIn("problem or workflow", feature)

    def test_status_and_architecture_docs_describe_the_current_release_model(self):
        production = PRODUCTION.read_text(encoding="utf-8")
        architecture = ARCHITECTURE.read_text(encoding="utf-8")
        troubleshooting = TROUBLESHOOTING.read_text(encoding="utf-8")
        self.assertIn("v2.2.1", production)
        self.assertIn("physical Unraid acceptance", production)
        self.assertNotIn("v2.2.0 candidate", production)
        self.assertIn("ADR-011", architecture)
        self.assertIn("ADR-012", architecture)
        self.assertNotIn("BT_CLIENT_MIN_REQUEST_GAP_MS", troubleshooting)

    def test_launch_materials_are_versioned_and_separate_from_release_gates(self):
        forum = ROOT / "docs" / "launch" / "UNRAID_FORUM.md"
        reddit = ROOT / "docs" / "launch" / "REDDIT.md"
        ca = ROOT / "docs" / "launch" / "COMMUNITY_APPLICATIONS.md"
        for path in (forum, reddit, ca):
            self.assertTrue(path.is_file(), path)
            source = path.read_text(encoding="utf-8")
            self.assertIn("v2.2.1", source)
        self.assertIn("CA listing is approved and searchable", reddit.read_text())

    def test_managed_guides_cover_the_complete_safe_lifecycle(self):
        for guide in (UNRAID, COMPOSE):
            source = guide.read_text(encoding="utf-8")
            with self.subTest(guide=guide.name):
                for command in (
                    "./btctl plan --env",
                    "./btctl install --env",
                    "./btctl doctor --env",
                    "./btctl uninstall --env",
                ):
                    self.assertIn(command, source)
                self.assertIn("./btctl upgrade", source)
                self.assertIn("./btctl rollback", source)
                self.assertIn("Acceptance checklist", source)

    def test_unraid_guide_covers_the_stock_host_bootstrap_boundary(self):
        readme = README.read_text(encoding="utf-8")
        unraid = UNRAID.read_text(encoding="utf-8")
        compatibility = COMPATIBILITY.read_text(encoding="utf-8")
        for contract in (
            "does not require host Python or NerdTools",
            "full Git checkout",
            "Docker socket is equivalent to root access",
            "build cache",
        ):
            self.assertIn(contract, unraid)
        self.assertIn("host Python or NerdTools", readme)
        self.assertIn("Unraid 7.3.2", compatibility)
        self.assertNotIn("`plan` does not mutate files or Docker", readme)

    def test_ca_guide_is_simple_exact_and_keeps_the_api_private(self):
        source = UNRAID_CA.read_text(encoding="utf-8")
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
            "not be published",
            "exact digest",
            "v2.2.1",
            "cwa_session",
            "/ajax/emailstat",
        ):
            self.assertIn(contract, source)
        self.assertNotIn(":latest", source)
        self.assertIn("DEPLOY_UNRAID_CA.md", README.read_text(encoding="utf-8"))

    def test_authentik_guide_is_fail_closed_and_edge_owned(self):
        source = AUTHENTIK.read_text(encoding="utf-8")
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
            "official security policy",
            "2026.2.5+",
            "2026.5.4+",
        ):
            self.assertIn(contract, source)
        self.assertNotIn("BT_AUTH_MODE=disabled", source)
        self.assertNotIn("BT_ALLOW_INSECURE_AUTH=true", source)

    def test_compatibility_matrix_makes_certified_scope_explicit(self):
        source = COMPATIBILITY.read_text(encoding="utf-8")
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
            self.assertIn(contract, source)

    def test_troubleshooting_routes_managed_failures_through_doctor(self):
        source = TROUBLESHOOTING.read_text(encoding="utf-8")
        self.assertIn("./btctl doctor --env", source)
        self.assertIn("/bt-config.json", source)
        self.assertIn("authentik-forwarded", source)
        self.assertIn("BT_IDENTITY_PROXY_IP", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
