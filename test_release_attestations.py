"""Behavioral tests for fail-closed release attestation validation."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "scripts" / "verify_release_attestations.py"
SOURCE_SHA = "a" * 40
BASE_DIGEST = "b" * 64
SOURCE_REPOSITORY = "https://github.com/felixapel/CWA-eBook-Translate-Plugin.git"
BASE_IMAGE = "python:3.11-alpine"
PLATFORMS = ("linux/amd64", "linux/arm64")


def valid_sbom():
    return {
        platform: {
            "SPDX": {
                "SPDXID": "SPDXRef-DOCUMENT",
                "spdxVersion": "SPDX-2.3",
                "dataLicense": "CC0-1.0",
                "packages": [
                    {"SPDXID": "SPDXRef-Package-python", "name": "python"}
                ],
            }
        }
        for platform in PLATFORMS
    }


def valid_provenance():
    return {
        platform: {
            "SLSA": {
                "buildDefinition": {
                    "buildType": "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md",
                    "externalParameters": {
                        "configSource": {
                            "uri": f"{SOURCE_REPOSITORY}#{SOURCE_SHA}",
                            "digest": {"sha1": SOURCE_SHA},
                            "path": "Dockerfile",
                        }
                    },
                    "resolvedDependencies": [
                        {
                            "uri": (
                                "pkg:docker/python@3.11-alpine?platform="
                                + platform.replace("/", "%2F")
                            ),
                            "digest": {"sha256": BASE_DIGEST},
                        },
                        {
                            "uri": f"{SOURCE_REPOSITORY}#{SOURCE_SHA}",
                            "digest": {"sha1": SOURCE_SHA},
                        },
                    ],
                },
                "runDetails": {"builder": {"id": "buildkit"}},
            }
        }
        for platform in PLATFORMS
    }


def valid_v02_provenance():
    return {
        platform: {
            "SLSA": {
                "buildType": "https://mobyproject.org/buildkit@v1",
                "invocation": {
                    "configSource": {
                        "uri": f"{SOURCE_REPOSITORY}#{SOURCE_SHA}",
                        "digest": {"sha1": SOURCE_SHA},
                        "entryPoint": "Dockerfile",
                    }
                },
                "materials": [
                    {
                        "uri": (
                            "pkg:docker/python@3.11-alpine?platform="
                            + platform.replace("/", "%2F")
                        ),
                        "digest": {"sha256": BASE_DIGEST},
                    },
                    {
                        "uri": f"{SOURCE_REPOSITORY}#{SOURCE_SHA}",
                        "digest": {"sha1": SOURCE_SHA},
                    },
                ],
                "builder": {"id": "buildkit"},
            }
        }
        for platform in PLATFORMS
    }


class ReleaseAttestationTests(unittest.TestCase):
    def run_validator(self, sbom, provenance, *, source_sha=SOURCE_SHA):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sbom_path = directory / "sbom.json"
            provenance_path = directory / "provenance.json"
            sbom_path.write_text(json.dumps(sbom))
            provenance_path.write_text(json.dumps(provenance))
            command = [
                sys.executable,
                str(SCRIPT),
                "--sbom", str(sbom_path),
                "--provenance", str(provenance_path),
                "--source-sha", source_sha,
                "--source-repository", SOURCE_REPOSITORY,
                "--base-image", BASE_IMAGE,
                "--base-digest", BASE_DIGEST,
            ]
            for platform in PLATFORMS:
                command.extend(("--platform", platform))
            return subprocess.run(
                command, check=False, capture_output=True, text=True
            )

    def test_accepts_complete_spdx_and_slsa_for_every_release_platform(self):
        for provenance in (valid_provenance(), valid_v02_provenance()):
            with self.subTest(provenance=provenance):
                result = self.run_validator(valid_sbom(), provenance)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("release attestations: OK", result.stdout)

    def test_rejects_missing_platform_or_empty_spdx_packages(self):
        missing = valid_sbom()
        del missing["linux/arm64"]
        empty = valid_sbom()
        empty["linux/amd64"]["SPDX"]["packages"] = []

        for sbom in (missing, empty):
            with self.subTest(sbom=sbom):
                result = self.run_validator(sbom, valid_provenance())
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.strip())
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_provenance_without_exact_source_or_base_digest(self):
        wrong_source = valid_provenance()
        source_slsa = wrong_source["linux/amd64"]["SLSA"]
        source_slsa["buildDefinition"]["externalParameters"]["configSource"][
            "digest"
        ]["sha1"] = "c" * 40
        source_slsa["metadata"] = {"irrelevant": SOURCE_SHA}
        wrong_base = valid_provenance()
        base_slsa = wrong_base["linux/arm64"]["SLSA"]
        base_slsa["buildDefinition"][
            "resolvedDependencies"
        ][0]["digest"]["sha256"] = "d" * 64
        base_slsa["metadata"] = {"irrelevant": BASE_DIGEST}

        for provenance in (wrong_source, wrong_base):
            with self.subTest(provenance=provenance):
                result = self.run_validator(valid_sbom(), provenance)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_expected_identities_only_in_unrelated_structures(self):
        wrong_source = valid_provenance()
        source = wrong_source["linux/amd64"]["SLSA"]["buildDefinition"]
        source["externalParameters"]["configSource"]["digest"]["sha1"] = "c" * 40
        # The source dependency remains correct, but configSource is authoritative.

        wrong_base = valid_provenance()
        dependencies = wrong_base["linux/arm64"]["SLSA"]["buildDefinition"][
            "resolvedDependencies"
        ]
        dependencies[0]["digest"]["sha256"] = "d" * 64
        dependencies.append({
            "uri": "pkg:docker/unrelated@latest",
            "digest": {"sha256": BASE_DIGEST},
        })

        for provenance in (wrong_source, wrong_base):
            with self.subTest(provenance=provenance):
                result = self.run_validator(valid_sbom(), provenance)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_invalid_expected_identity_without_a_traceback(self):
        result = self.run_validator(
            valid_sbom(), valid_provenance(), source_sha="not-a-commit"
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("source SHA", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
