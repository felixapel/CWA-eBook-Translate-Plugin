"""Security contracts for the published image and recommended topology."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class ContainerContractTests(unittest.TestCase):
    def test_image_declares_the_existing_stable_non_root_identity(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("addgroup -S -g 102 appuser", dockerfile)
        self.assertIn("adduser -S -D -H -u 101 -G appuser appuser", dockerfile)
        self.assertRegex(dockerfile, r"(?m)^USER appuser$")
        self.assertNotIn('VOLUME ["/app/data"]', dockerfile)
        for obsolete in ("gosu", "shadow=", "linux-pam=", "chown -R"):
            self.assertNotIn(obsolete, dockerfile)

    def test_entrypoint_never_changes_ownership_or_escalates(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
        for forbidden in ("gosu", "chown", "appuser gunicorn"):
            self.assertNotIn(forbidden, entrypoint)
        self.assertIn('BT_ROLE="${BT_ROLE:-auto}"', entrypoint)
        self.assertIn('exec gunicorn --bind', entrypoint)
        self.assertIn('exec nginx -c /app/proxy/nginx-main.conf', entrypoint)

    def test_non_root_nginx_writes_only_below_tmp(self):
        config = (ROOT / "proxy" / "nginx-main.conf").read_text()
        for directive in (
            "pid /tmp/nginx/nginx.pid;",
            "client_body_temp_path /tmp/nginx/client_temp;",
            "proxy_temp_path /tmp/nginx/proxy_temp;",
            "fastcgi_temp_path /tmp/nginx/fastcgi_temp;",
            "uwsgi_temp_path /tmp/nginx/uwsgi_temp;",
            "scgi_temp_path /tmp/nginx/scgi_temp;",
            "access_log /dev/stdout;",
            "error_log /dev/stderr warn;",
            "include /tmp/nginx/proxy.conf;",
        ):
            self.assertIn(directive, config)
        self.assertNotRegex(config, r"(?m)^\s*user\s+")

    def test_proxy_backend_connections_have_a_bounded_timeout(self):
        template = (ROOT / "proxy" / "nginx.conf.template").read_text()
        self.assertGreaterEqual(template.count("proxy_connect_timeout 2s;"), 2)

    def test_compose_recommends_independent_hardened_roles(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertRegex(compose, r"(?m)^  book-translator-api:$")
        self.assertRegex(compose, r"(?m)^  book-translator-proxy:$")
        self.assertIn("BT_ROLE=api", compose)
        self.assertIn("BT_ROLE=proxy", compose)
        self.assertIn("BT_API_UPSTREAM=http://book-translator-api:8390", compose)
        self.assertGreaterEqual(compose.count("read_only: true"), 2)
        self.assertGreaterEqual(compose.count("no-new-privileges:true"), 2)
        self.assertGreaterEqual(compose.count("cap_drop:"), 2)
        self.assertGreaterEqual(compose.count("- ALL"), 2)
        self.assertGreaterEqual(compose.count("/tmp:rw,noexec,nosuid"), 2)

    def test_ci_runs_both_roles_with_the_production_sandbox(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        smoke_path = ROOT / "scripts" / "container-smoke.sh"
        smoke = smoke_path.read_text()
        self.assertTrue(smoke_path.stat().st_mode & 0o111)
        self.assertIn('./scripts/container-smoke.sh "$SMOKE_IMAGE" "$SMOKE_PREFIX"', workflow)
        self.assertIn('docker rm -f -v "$PROXY_CONTAINER" "$API_CONTAINER"', smoke)
        for token in (
            "BT_ROLE=api",
            "BT_ROLE=proxy",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges:true",
            "docker image inspect \"$SMOKE_IMAGE\" --format '{{.Config.User}}'",
        ):
            self.assertIn(token, smoke)
        self.assertNotIn("gosu", smoke)

    def test_unraid_helpers_preserve_the_non_root_sandbox(self):
        deploy = (ROOT / "deploy_unraid.sh").read_text()
        template = (ROOT / "my-book-translator-api.xml.tmpl").read_text()
        for token in (
            "BT_ROLE=api",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "uid=101,gid=102",
        ):
            self.assertIn(token, deploy)
            self.assertIn(token, template)
        self.assertIn("install -d -m 0700 -o 101 -g 102 --", deploy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
