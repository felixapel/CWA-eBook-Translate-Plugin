"""Contracts keeping the supported install and recovery documentation executable."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parent
README = ROOT / "README.md"
UNRAID = ROOT / "docs" / "DEPLOY_UNRAID.md"
COMPOSE = ROOT / "docs" / "DEPLOY_COMPOSE.md"
AUTHENTIK = ROOT / "docs" / "AUTHENTIK.md"
COMPATIBILITY = ROOT / "docs" / "COMPATIBILITY.md"
TROUBLESHOOTING = ROOT / "docs" / "TROUBLESHOOTING.md"


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
