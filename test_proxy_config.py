"""Fail-closed rendering contracts for the nginx injection proxy."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
RENDERER = ROOT / "proxy" / "render_config.py"
TEMPLATE = ROOT / "proxy" / "nginx.conf.template"


class ProxyConfigRendererTests(unittest.TestCase):
    def render(self, overrides: dict[str, str | None] | None = None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "proxy.conf"
        env = os.environ.copy()
        env.update({
            "CWA_UPSTREAM": "http://calibre-web:8083",
            "BT_API_UPSTREAM": "http://book-translator-api:8390",
            "BT_PROXY_PORT": "8080",
            "BT_UI_VERSION": "2.1.4",
            "BT_PUBLIC_ORIGIN": "https://books.example.test:8443",
            "BT_CWA_MAX_BODY_SIZE": "2g",
            "BT_CWA_IDENTITY_HEADER": "Remote-User",
        })
        for name, value in (overrides or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEMPLATE), str(output)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        return result, output

    def test_valid_contract_renders_only_validated_values(self):
        result, output = self.render()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rendered = output.read_text()
        self.assertEqual(rendered.count("proxy_set_header Host books.example.test:8443;"), 2)
        self.assertEqual(rendered.count("proxy_set_header X-Forwarded-Proto https;"), 2)
        self.assertEqual(rendered.count("proxy_set_header X-Forwarded-For $remote_addr;"), 2)
        self.assertIn("client_max_body_size 2g;", rendered)
        self.assertIn("absolute_redirect off;", rendered)
        self.assertIn("listen 8080;", rendered)
        self.assertIn("proxy_pass http://calibre-web:8083;", rendered)
        self.assertIn("proxy_pass http://book-translator-api:8390/;", rendered)
        self.assertIn('proxy_set_header Remote-User "";', rendered)
        self.assertNotIn("$http_x_forwarded_proto", rendered)
        self.assertNotRegex(rendered, r"\$\{(?:BT_|CWA_)")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_public_origin_is_required_and_must_be_an_exact_http_origin(self):
        for value in (
            None,
            "",
            "books.example.test",
            "file://books.example.test",
            "https://user:pass@books.example.test",
            "https://books.example.test/path",
            "https://books.example.test?query=1",
            "https://books.example.test#fragment",
            "https://books.example.test\nserver { listen 9000; }",
        ):
            with self.subTest(value=value):
                result, output = self.render({"BT_PUBLIC_ORIGIN": value})
                self.assertEqual(result.returncode, 78)
                self.assertFalse(output.exists())
                self.assertIn("BT_PUBLIC_ORIGIN", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_upstream_urls_reject_credentials_paths_and_non_http_schemes(self):
        cases = (
            ("CWA_UPSTREAM", "file:///etc/passwd"),
            ("CWA_UPSTREAM", "http://calibre-web:8083/admin"),
            ("CWA_UPSTREAM", "http://user:secret@calibre-web:8083"),
            ("BT_API_UPSTREAM", "http://api:8390/translate"),
            ("BT_API_UPSTREAM", "http://api:bad"),
            ("BT_API_UPSTREAM", "http://api:8390\ninclude /tmp/evil.conf"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                result, output = self.render({name: value})
                self.assertEqual(result.returncode, 78)
                self.assertFalse(output.exists())
                self.assertIn(name, result.stderr)
                self.assertNotIn("secret", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_port_size_and_ui_version_are_bounded_tokens(self):
        cases = (
            ("BT_PROXY_PORT", "0"),
            ("BT_PROXY_PORT", "65536"),
            ("BT_PROXY_PORT", "8080; include /tmp/evil"),
            ("BT_CWA_MAX_BODY_SIZE", "0"),
            ("BT_CWA_MAX_BODY_SIZE", "unlimited"),
            ("BT_CWA_MAX_BODY_SIZE", "2g; include /tmp/evil"),
            ("BT_UI_VERSION", "../../secret"),
            ("BT_UI_VERSION", "v1\nscript"),
            ("BT_CWA_IDENTITY_HEADER", "Remote_User"),
            ("BT_CWA_IDENTITY_HEADER", "Remote-User; include"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                result, output = self.render({name: value})
                self.assertEqual(result.returncode, 78)
                self.assertFalse(output.exists())
                self.assertIn(name, result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
