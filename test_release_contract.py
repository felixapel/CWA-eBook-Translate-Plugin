"""Fail-closed contracts for production image releases.

The integration tests build tiny local Git repositories, so they exercise the
same annotated-tag, ancestry, and peeled-mirror checks as the release runner
without contacting either Gitea or GitHub.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "scripts" / "release_preflight.py"
TAG_SCRIPT = ROOT / "scripts" / "release_image_tags.py"
GITEA_RELEASE = ROOT / ".gitea" / "workflows" / "release.yml"
GITEA_CI = ROOT / ".gitea" / "workflows" / "ci.yml"
GITHUB_RELEASE = ROOT / ".github" / "workflows" / "release.yml"
GITHUB_CI = ROOT / ".github" / "workflows" / "ci.yml"
VERSION = "2.1.4"
TAG = f"v{VERSION}"


class ReleaseRepository:
    """A disposable repository and bare public mirror for preflight tests."""

    def __init__(self, root: Path, versions: dict[str, str] | None = None):
        self.repo = root / "source"
        self.mirror = root / "mirror.git"
        self.repo.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Release Contract Test")
        self._git("config", "user.email", "release-contract@example.invalid")
        values = {
            "version_file": VERSION,
            "package": VERSION,
            "lock": VERSION,
            "lock_root": VERSION,
            "ui": VERSION,
            "overlay_css": VERSION,
            "overlay_js": VERSION,
            "changelog": VERSION,
        }
        values.update(versions or {})
        self.write_version_surfaces(values)
        self._git("add", ".")
        self._git("commit", "-m", "release fixture")
        self.sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("tag", "-a", TAG, "-m", TAG)
        subprocess.run(
            ["git", "init", "--bare", str(self.mirror)],
            check=True,
            capture_output=True,
            text=True,
        )
        self._git("remote", "add", "mirror", str(self.mirror))
        self._git("push", "mirror", "main", TAG)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def write_version_surfaces(self, values: dict[str, str]) -> None:
        (self.repo / "static").mkdir(exist_ok=True)
        (self.repo / "VERSION").write_text(values["version_file"] + "\n")
        (self.repo / "package.json").write_text(json.dumps({
            "name": "release-fixture",
            "version": values["package"],
        }))
        (self.repo / "package-lock.json").write_text(json.dumps({
            "name": "release-fixture",
            "version": values["lock"],
            "lockfileVersion": 3,
            "packages": {"": {
                "name": "release-fixture",
                "version": values["lock_root"],
            }},
        }))
        (self.repo / "static" / "translator.js").write_text(
            f"const BT_UI_VERSION = '{values['ui']}';\n"
        )
        (self.repo / "overlay").mkdir(exist_ok=True)
        (self.repo / "overlay" / "read.html").write_text(
            f'<link href="/static/css/translator.css?v={values["overlay_css"]}">\n'
            f'<script src="/static/js/translator.js?v={values["overlay_js"]}"></script>\n'
        )
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\n"
            f"## [{values['changelog']}] - 2026-07-12\n"
        )

    def preflight(
        self,
        *,
        tag: str = TAG,
        sha: str | None = None,
        main_ref: str = "refs/heads/main",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repository",
                str(self.repo),
                "--tag",
                tag,
                "--sha",
                sha or self.sha,
                "--main-ref",
                main_ref,
                "--mirror-url",
                str(self.mirror),
            ],
            check=False,
            capture_output=True,
            text=True,
        )


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def fixture(self, versions: dict[str, str] | None = None) -> ReleaseRepository:
        return ReleaseRepository(Path(self.tempdir.name), versions)

    def assert_failed(self, result: subprocess.CompletedProcess[str], fragment: str):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(fragment, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_matching_annotated_tag_on_main_and_mirror(self):
        fixture = self.fixture()

        result = fixture.preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], VERSION)
        self.assertEqual(payload["tag"], TAG)
        self.assertEqual(payload["sha"], fixture.sha)
        self.assertEqual(payload["mirror_sha"], fixture.sha)

    def test_rejects_non_semver_tag_before_git_ref_interpretation(self):
        fixture = self.fixture()

        result = fixture.preflight(tag="v2.1.4^{commit}")

        self.assert_failed(result, "valid SemVer release tag")

    def test_rejects_each_version_surface_mismatch(self):
        for surface in (
            "version_file",
            "package",
            "lock",
            "lock_root",
            "ui",
            "overlay_css",
            "overlay_js",
            "changelog",
        ):
            with self.subTest(surface=surface):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ReleaseRepository(
                        Path(directory), {surface: "9.9.9"}
                    )
                    result = fixture.preflight()
                self.assert_failed(result, surface)

    def test_rejects_checkout_sha_mismatch(self):
        fixture = self.fixture()

        result = fixture.preflight(sha="0" * 40)

        self.assert_failed(result, "checked-out commit")

    def test_rejects_lightweight_release_tag(self):
        fixture = self.fixture()
        fixture._git("tag", "-d", TAG)
        fixture._git("tag", TAG)

        result = fixture.preflight()

        self.assert_failed(result, "annotated tag")

    def test_rejects_tag_commit_outside_main(self):
        fixture = self.fixture()
        fixture._git("switch", "-c", "unmerged")
        (fixture.repo / "unmerged.txt").write_text("not on main\n")
        fixture._git("add", "unmerged.txt")
        fixture._git("commit", "-m", "unmerged release")
        fixture.sha = fixture._git("rev-parse", "HEAD").stdout.strip()
        fixture._git("tag", "-f", "-a", TAG, "-m", TAG)
        fixture._git("push", "--force", "mirror", TAG)

        result = fixture.preflight()

        self.assert_failed(result, "not reachable from")

    def test_rejects_missing_mirror_tag(self):
        fixture = self.fixture()
        fixture._git("--git-dir", str(fixture.mirror), "update-ref", "-d", f"refs/tags/{TAG}")

        result = fixture.preflight()

        self.assert_failed(result, "mirror tag")

    def test_rejects_lightweight_mirror_tag(self):
        fixture = self.fixture()
        fixture._git(
            "--git-dir",
            str(fixture.mirror),
            "update-ref",
            f"refs/tags/{TAG}",
            fixture.sha,
        )

        result = fixture.preflight()

        self.assert_failed(result, "same annotated tag")

    def test_rejects_mirror_tag_pointing_to_a_different_commit(self):
        fixture = self.fixture()
        (fixture.repo / "later.txt").write_text("different mirror commit\n")
        fixture._git("add", "later.txt")
        fixture._git("commit", "-m", "different mirror commit")
        later_sha = fixture._git("rev-parse", "HEAD").stdout.strip()
        fixture._git("tag", "-a", "mirror-other", "-m", "mirror-other")
        fixture._git("push", "mirror", "main", "mirror-other")
        other_tag_object = fixture._git(
            "rev-parse", "refs/tags/mirror-other"
        ).stdout.strip()
        fixture._git(
            "--git-dir",
            str(fixture.mirror),
            "update-ref",
            f"refs/tags/{TAG}",
            other_tag_object,
        )
        self.assertNotEqual(later_sha, fixture.sha)
        fixture._git("checkout", "--detach", fixture.sha)

        result = fixture.preflight()

        self.assert_failed(result, "mirror tag commit")

    def test_rejects_different_annotated_tag_object_for_same_commit(self):
        fixture = self.fixture()
        fixture._git(
            "tag", "-a", "mirror-other", "-m", "different annotation", fixture.sha
        )
        fixture._git("push", "mirror", "mirror-other")
        other_tag_object = fixture._git(
            "rev-parse", "refs/tags/mirror-other"
        ).stdout.strip()
        fixture._git(
            "--git-dir",
            str(fixture.mirror),
            "update-ref",
            f"refs/tags/{TAG}",
            other_tag_object,
        )

        result = fixture.preflight()

        self.assert_failed(result, "mirror tag object")


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_changelog_has_one_active_unreleased_section(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        self.assertEqual(changelog.count("\n## [Unreleased]\n"), 1)

    def test_gitea_is_the_only_release_authority(self):
        self.assertTrue(GITEA_RELEASE.is_file())
        self.assertTrue(GITEA_CI.is_file())
        self.assertFalse(GITHUB_RELEASE.exists())

    def test_gitea_workflow_directory_keeps_mirror_ci_in_lockstep(self):
        # Gitea uses the first existing workflow directory. Once .gitea exists,
        # a missing CI copy would silently remove every normal push/PR gate.
        self.assertEqual(GITEA_CI.read_text(), GITHUB_CI.read_text())

    def test_publish_is_gated_by_preflight_and_all_artifact_checks(self):
        workflow = GITEA_RELEASE.read_text()
        self.assertIn("needs: [preflight, backend, frontend, docker-smoke]", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("scripts/release_preflight.py", workflow)
        self.assertIn(
            "https://github.com/felixapel/CWA-eBook-Translate-Plugin.git",
            workflow,
        )
        self.assertNotIn("continue-on-error", workflow)

    def test_unverified_tag_code_cannot_run_before_trusted_preflight(self):
        workflow = GITEA_RELEASE.read_text()
        self.assertIn("ref: main", workflow)
        self.assertIn("path: trusted", workflow)
        self.assertIn("path: candidate", workflow)
        self.assertIn("python3 trusted/scripts/release_preflight.py", workflow)
        self.assertIn("timeout 60 git -C candidate fetch", workflow)
        for job in ("backend", "frontend", "docker-smoke"):
            self.assertRegex(
                workflow,
                rf"(?m)^  {re.escape(job)}:\n    needs: preflight$",
            )

    def test_gitea_release_uses_only_supported_conditional_expression(self):
        workflow = GITEA_RELEASE.read_text()
        conditional_lines = [
            line.strip() for line in workflow.splitlines()
            if line.strip().startswith("if:")
        ]
        self.assertTrue(conditional_lines)
        self.assertEqual(set(conditional_lines), {"if: always()"})
        self.assertNotIn("concurrency:", workflow)
        self.assertNotIn("timeout-minutes:", workflow)

    def test_release_reuses_every_required_ci_contract(self):
        workflow = GITEA_RELEASE.read_text()
        for command in (
            "python3 test_translation.py",
            "python3 test_hardening.py",
            "test_release_contract",
            "npm ci",
            "npm audit --audit-level=high",
            "npm test",
            "docker version",
            'docker build -t "$SMOKE_IMAGE" .',
            './scripts/container-smoke.sh "$SMOKE_IMAGE" "$SMOKE_PREFIX"',
        ):
            self.assertIn(command, workflow)

    def test_release_docker_jobs_use_the_host_runner_and_scoped_names(self):
        workflow = GITEA_RELEASE.read_text()
        self.assertRegex(
            workflow,
            r"(?m)^  docker-smoke:\n    needs: preflight\n"
            r"(?:    #[^\n]*\n)*    runs-on: weebdb-docker$",
        )
        self.assertRegex(
            workflow,
            r"(?m)^  publish:\n"
            r"    needs: \[preflight, backend, frontend, docker-smoke\]\n"
            r"(?:    #[^\n]*\n)*    runs-on: weebdb-docker$",
        )
        self.assertIn("sh scripts/ci-docker-names.sh", workflow)
        self.assertNotIn("bt-release-smoke-${{ gitea.run_id }}", workflow)
        self.assertNotIn("bt-release-audit:${{ gitea.run_id }}", workflow)

    def test_one_build_publishes_all_requested_registry_tags(self):
        workflow = GITEA_RELEASE.read_text()
        self.assertEqual(workflow.count("uses: docker/build-push-action@"), 1)
        self.assertIn("GHCR_USERNAME: ${{ secrets.GHCR_USERNAME }}", workflow)
        self.assertIn("GHCR_TOKEN: ${{ secrets.GHCR_TOKEN }}", workflow)
        self.assertIn("DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}", workflow)
        self.assertIn("DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}", workflow)
        self.assertIn(
            "DOCKER_CONFIG: ${{ gitea.workspace }}/.docker-release", workflow
        )
        self.assertIn('rm -rf -- "$DOCKER_CONFIG"', workflow)
        self.assertIn("platforms: linux/amd64,linux/arm64", workflow)
        self.assertIn("push: true", workflow)


class ReleaseImageTagTests(unittest.TestCase):
    def tags(self, tag: str, *images: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(TAG_SCRIPT), "--tag", tag]
        for image in images:
            command.extend(("--image", image))
        return subprocess.run(command, check=False, capture_output=True, text=True)

    def test_stable_release_moves_full_minor_and_latest_aliases(self):
        result = self.tags(
            "v2.3.4",
            "ghcr.io/felixapel/cwa-ebook-translate-plugin",
            "docker.io/felixapel/cwa-ebook-translate-plugin",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "ghcr.io/felixapel/cwa-ebook-translate-plugin:2.3.4",
            "ghcr.io/felixapel/cwa-ebook-translate-plugin:2.3",
            "ghcr.io/felixapel/cwa-ebook-translate-plugin:latest",
            "docker.io/felixapel/cwa-ebook-translate-plugin:2.3.4",
            "docker.io/felixapel/cwa-ebook-translate-plugin:2.3",
            "docker.io/felixapel/cwa-ebook-translate-plugin:latest",
        ])

    def test_prerelease_only_publishes_the_immutable_full_version(self):
        result = self.tags(
            "v2.3.4-rc.1", "ghcr.io/felixapel/cwa-ebook-translate-plugin"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "ghcr.io/felixapel/cwa-ebook-translate-plugin:2.3.4-rc.1"
        ])

    def test_rejects_invalid_tag_or_image_without_a_traceback(self):
        for tag, image in (
            ("2.3.4", "ghcr.io/felixapel/plugin"),
            ("v2.3.4", "GHCR.IO/Felix/Plugin"),
            ("v2.3.4", "ghcr.io/felix/plugin:old"),
        ):
            with self.subTest(tag=tag, image=image):
                result = self.tags(tag, image)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.strip())
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
