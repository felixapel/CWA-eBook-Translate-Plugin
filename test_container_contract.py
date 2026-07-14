"""Security contracts for the built image and recommended topology."""
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
        self.assertNotIn("COPY *.py", dockerfile)
        for runtime_module in (
            "auth.py", "cache.py", "server.py", "singleflight.py",
            "translator.py", "work_budget.py",
        ):
            self.assertIn(runtime_module, dockerfile)

    def test_entrypoint_never_changes_ownership_or_escalates(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
        for forbidden in ("gosu", "chown", "appuser gunicorn"):
            self.assertNotIn(forbidden, entrypoint)
        self.assertIn('BT_ROLE="${BT_ROLE:-auto}"', entrypoint)
        self.assertIn('exec gunicorn --bind', entrypoint)
        self.assertIn('exec nginx -c /app/proxy/nginx-main.conf', entrypoint)
        self.assertIn("umask 027", entrypoint)
        self.assertIn('stat -c %a /app/data', entrypoint)
        self.assertNotIn("chmod 700 /app/data", entrypoint)

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

    def test_proxy_uses_validated_origin_and_sanitized_forwarding(self):
        template = (ROOT / "proxy" / "nginx.conf.template").read_text()
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
        compose = (ROOT / "docker-compose.yml").read_text()
        smoke = (ROOT / "scripts" / "container-smoke.sh").read_text()

        self.assertNotIn("client_max_body_size 0;", template)
        self.assertIn("client_max_body_size ${BT_CWA_MAX_BODY_SIZE};", template)
        self.assertIn("absolute_redirect off;", template)
        self.assertNotIn("$http_x_forwarded_proto", template)
        self.assertEqual(
            template.count("proxy_set_header Host ${BT_PUBLIC_HOST};"), 2
        )
        self.assertEqual(
            template.count("proxy_set_header X-Forwarded-Proto ${BT_PUBLIC_SCHEME};"),
            2,
        )
        self.assertEqual(
            template.count("proxy_set_header X-Forwarded-For $remote_addr;"), 2
        )
        self.assertIn("proxy/render_config.py", entrypoint)
        self.assertNotIn("envsubst", entrypoint)
        self.assertIn(
            "BT_PUBLIC_ORIGIN=${BT_PUBLIC_ORIGIN:?Set BT_PUBLIC_ORIGIN to the exact browser-facing origin}",
            compose,
        )
        self.assertNotIn("BT_PUBLIC_ORIGIN=${BT_PUBLIC_ORIGIN:-", compose)
        self.assertIn("BT_CWA_MAX_BODY_SIZE=${BT_CWA_MAX_BODY_SIZE:-2g}", compose)
        self.assertIn("BT_CWA_IDENTITY_HEADER=${BT_CWA_IDENTITY_HEADER:-Remote-User}", compose)
        self.assertIn('proxy_set_header ${BT_CWA_IDENTITY_HEADER} "";', template)
        self.assertIn("BT_PUBLIC_ORIGIN=https://books.example.test:8443", smoke)

    def test_compose_recommends_independent_hardened_roles(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertEqual(compose.count("    build: .\n"), 1)
        self.assertEqual(
            compose.count("    image: cwa-ebook-translate-plugin:local\n"),
            2,
        )
        self.assertRegex(compose, r"(?m)^  book-translator-api:$")
        self.assertRegex(compose, r"(?m)^  book-translator-proxy:$")
        self.assertIn("BT_ROLE=api", compose)
        self.assertIn("BT_ROLE=proxy", compose)
        self.assertIn("BT_API_UPSTREAM=http://translator-api:8390", compose)
        self.assertIn("- translator-api", compose)
        self.assertIn("BT_TRUSTED_PROXIES=172.30.39.3/32", compose)
        self.assertIn("- subnet: 172.30.39.0/24", compose)
        self.assertIn("BT_AUTH_MODE=cwa_session", compose)
        self.assertIn("BT_CWA_AUTH_URL=http://calibre-web:8083/ajax/emailstat", compose)
        self.assertIn("BT_ALLOW_PRIVATE_LAN=false", compose)
        api_service = compose.split("  book-translator-api:", 1)[1].split(
            "  book-translator-proxy:", 1
        )[0]
        self.assertIn("cwa-net:", api_service)
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
        self.assertIn("docker rm -f -v", smoke)
        for container in (
            "$EDGE_CONTAINER",
            "$OUTPOST_CONTAINER",
            "$PROXY_CONTAINER",
            "$API_CONTAINER",
        ):
            self.assertIn(container, smoke)
        for token in (
            "BT_ROLE=api",
            "BT_ROLE=proxy",
            "BT_AUTH_MODE=token",
            "BT_API_TOKEN=${SMOKE_TOKEN}",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges:true",
            "docker image inspect \"$SMOKE_IMAGE\" --format '{{.Config.User}}'",
        ):
            self.assertIn(token, smoke)
        self.assertNotIn("gosu", smoke)

    def test_lifecycle_smoke_can_remove_non_root_managed_data(self):
        smoke = (ROOT / "scripts" / "btctl-lifecycle-smoke.sh").read_text()
        cleanup = smoke.split("cleanup() {", 1)[1].split("}\ntrap cleanup EXIT", 1)[0]
        self.assertIn("docker run --rm --user 0:0", cleanup)
        self.assertIn("type=bind,src=${ROOT_DIR},dst=/cleanup", cleanup)
        self.assertIn("chmod -R u+rwX,g+rwX /cleanup", cleanup)
        self.assertLess(
            cleanup.index("chmod -R u+rwX,g+rwX /cleanup"),
            cleanup.index('rm -rf -- "$ROOT_DIR"'),
        )

    def test_lifecycle_cwa_fixture_is_literal_compilable_python(self):
        smoke = (ROOT / "scripts" / "btctl-lifecycle-smoke.sh").read_text()
        marker = 'cat >"$CWA_FIXTURE" <<\'PY\'\n'
        self.assertIn(marker, smoke)
        fixture = smoke.split(marker, 1)[1].split("\nPY\n", 1)[0]
        compile(fixture, "cwa-lifecycle-fixture.py", "exec")
        self.assertIn(
            '--mount "type=bind,src=${CWA_FIXTURE},dst=/app/cwa-fixture.py,readonly"',
            smoke,
        )
        self.assertIn(
            '--entrypoint python "$CWA_IMAGE" /app/cwa-fixture.py',
            smoke,
        )

    def test_image_auth_defaults_fail_closed_and_proxy_forwards_cwa_cookie(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
        proxy = (ROOT / "proxy" / "nginx.conf.template").read_text()
        self.assertNotRegex(dockerfile, r"(?m)^ENV BT_(?:API_TOKEN|AUTH_MODE)=")
        self.assertIn('mode="${BT_AUTH_MODE:-token}"', entrypoint)
        self.assertIn("validate_api_auth", entrypoint)
        self.assertIn("BT_API_TOKEN is required", entrypoint)
        self.assertIn("disabled auth requires BT_ALLOW_INSECURE_AUTH=true", entrypoint)
        self.assertIn("proxy_set_header Cookie $http_cookie;", proxy)
        self.assertIn('proxy_set_header ${BT_CWA_IDENTITY_HEADER} "";', proxy)
        self.assertIn('proxy_set_header X-BT-Subject "";', proxy)
        self.assertIn('proxy_set_header X-BT-Roles "";', proxy)

    def test_unraid_helpers_preserve_the_non_root_sandbox(self):
        adapter = (ROOT / "btctl_unraid.py").read_text()
        api_template = (
            ROOT / "deploy" / "unraid" / "my-cwa-translate-api.xml.tmpl"
        ).read_text()
        proxy_template = (
            ROOT / "deploy" / "unraid" / "my-cwa-translate-proxy.xml.tmpl"
        ).read_text()
        for token in (
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "uid=101,gid=102",
        ):
            self.assertIn(token, api_template)
            self.assertIn(token, proxy_template)
        self.assertIn('"BT_ROLE": "api"', adapter)
        self.assertIn(
            "os.chown(candidate, 101, 102, follow_symlinks=False)", adapter
        )
        self.assertIn("publish_port=None", adapter)


if __name__ == "__main__":
    unittest.main(verbosity=2)
