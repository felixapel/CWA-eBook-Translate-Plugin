"""Authentik identity-edge configuration contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from btctl_auth import render_authentik_edge
from btctl_core import ConfigError, DeploymentPlan, InstallConfig, ReleaseIdentity


def values(root: Path, reverse_proxy: str) -> dict[str, str]:
    return {
        "BT_INSTALL_PROFILE": "compose-existing",
        "BT_INSTALL_NAME": "cwa-translate-test",
        "BT_INGRESS_MODE": "docker-edge",
        "BT_PROXY_PORT": "",
        "BT_EDGE_NETWORK": "authentik_backend",
        "BT_AUTH_PROFILE": "authentik-forwarded",
        "BT_PUBLIC_ORIGIN": "https://books.example.test",
        "CWA_UPSTREAM": "http://calibre-web-automated:8083",
        "BT_CWA_CONTAINER": "calibre-web-automated",
        "BT_CWA_NETWORK": "cwa_default",
        "BT_CWA_VERSION": "4.0.6",
        "BT_STATE_DIR": str(root / "state"),
        "BT_DATA_DIR": str(root / "data"),
        "BT_BACKUP_DIR": str(root / "backups"),
        "BT_IDENTITY_PROXY_IP": "172.30.50.9/32",
        "BT_AUTHENTIK_VERSION": "2026.5.4",
        "BT_AUTHENTIK_OUTPOST_URL": "http://authentik-outpost:9000",
        "BT_REVERSE_PROXY": reverse_proxy,
        "LLM_PROVIDER": "local",
        "LLM_MODEL": "gemma4-12b",
        "BT_LOCAL_URL": "http://host.docker.internal:2819/v1/chat/completions",
        "LLM_API_KEY": "",
    }


class AuthentikEdgeConfigTests(unittest.TestCase):
    def setUp(self):
        self.identity = ReleaseIdentity.from_checkout(
            version="2.2.0", sha="f" * 40, clean=True
        )

    def test_forwarded_profile_requires_an_exact_outpost_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            base = values(Path(directory), "nginx")
            for outpost in ("", "authentik:9000", "http://user:pass@authentik:9000"):
                with self.subTest(outpost=outpost), self.assertRaisesRegex(
                    ConfigError, "BT_AUTHENTIK_OUTPOST_URL"
                ):
                    InstallConfig.from_mapping(
                        {**base, "BT_AUTHENTIK_OUTPOST_URL": outpost}, self.identity
                    )

            for outpost in (
                "http://$http_host",
                "http://%24http_host",
                "http://authentik-outpost;evil:9000",
                "http://authentik-outpost\\evil:9000",
            ):
                with self.subTest(outpost=outpost), self.assertRaisesRegex(
                    ConfigError, "BT_AUTHENTIK_OUTPOST_URL"
                ):
                    InstallConfig.from_mapping(
                        {**base, "BT_AUTHENTIK_OUTPOST_URL": outpost}, self.identity
                    )

    def test_each_supported_edge_is_identity_overwriting_and_cookie_stripping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for reverse_proxy in ("nginx", "traefik", "caddy"):
                with self.subTest(reverse_proxy=reverse_proxy):
                    config = InstallConfig.from_mapping(
                        values(root, reverse_proxy), self.identity
                    )
                    plan = DeploymentPlan.from_config(config)
                    artifact = render_authentik_edge(config, plan)
                    content = artifact.content

                    self.assertIn("http://cwa-translate-test-api:8390", content)
                    self.assertIn("authentik-uid", content.lower())
                    self.assertIn("Cookie", content)
                    self.assertIn("X-BT-Subject", content)
                    self.assertNotIn("LLM_API_KEY", content)
                    self.assertNotIn("authentik-groups", content.lower())
                    self.assertTrue(artifact.filename.startswith("authentik-edge."))
                    self.assertIn(
                        "/outpost.goauthentik.io/auth/", content
                    )

    def test_plan_declares_the_generated_edge_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root, "nginx"), self.identity)
            plan = DeploymentPlan.from_config(config)

            self.assertEqual(
                plan.resources["identity_edge_config"]["path"],
                str(root / "state" / "authentik-edge.nginx.conf"),
            )
            self.assertEqual(
                plan.resources["identity_edge_config"]["ownership"], "owned"
            )

    def test_nginx_edge_preserves_authentik_login_and_cookie_refresh_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = InstallConfig.from_mapping(values(root, "nginx"), self.identity)
            content = render_authentik_edge(
                config, DeploymentPlan.from_config(config)
            ).content

            self.assertIn("error_page 401 = @goauthentik_proxy_signin;", content)
            self.assertIn(
                "auth_request_set $bt_auth_cookie $upstream_http_set_cookie;",
                content,
            )
            self.assertIn("add_header Set-Cookie $bt_auth_cookie;", content)
            self.assertNotIn('if ($bt_authentik_uid = "")', content)
            self.assertIn("proxy_set_header Host books.example.test;", content)
            self.assertIn(
                "proxy_set_header X-Original-URL https://books.example.test$request_uri;",
                content,
            )
            outpost_location = content.split(
                "location = /outpost.goauthentik.io/auth/nginx {", 1
            )[1]
            for header in ("X-authentik-uid", "X-BT-Subject", "X-BT-Roles"):
                clear = f'proxy_set_header {header} "";'
                self.assertIn(clear, outpost_location)
                self.assertLess(
                    outpost_location.index(clear),
                    outpost_location.index("proxy_pass "),
                )

    def test_nginx_requires_an_internal_http_outpost_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = values(Path(directory), "nginx")
            configured["BT_AUTHENTIK_OUTPOST_URL"] = "https://authentik-outpost:9443"

            with self.assertRaisesRegex(ConfigError, "Nginx.*http"):
                InstallConfig.from_mapping(configured, self.identity)


if __name__ == "__main__":
    unittest.main(verbosity=2)
