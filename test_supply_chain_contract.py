"""Fail-closed contracts for immutable third-party build inputs."""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
