"""Small, shell-free Docker CLI adapter used by btctl lifecycle code."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


class DockerCommandError(RuntimeError):
    """Docker could not satisfy an operation; command output stays private."""


class DockerCLI:
    def _run(
        self,
        arguments: list[str],
        *,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["docker", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerCommandError("Docker command could not be executed") from exc
        if check and result.returncode != 0:
            # Compose validation can echo environment values. Do not copy its
            # stdout/stderr into exceptions or logs; operators can rerun the
            # exact documented command locally when deeper diagnostics matter.
            operation = arguments[0] if arguments else "operation"
            raise DockerCommandError(f"Docker {operation} failed")
        return result

    def require_available(self) -> None:
        self._run(["version", "--format", "{{.Server.Version}}"], timeout=20)

    def _inspect(self, kind: str, name: str) -> dict | None:
        if kind == "network":
            arguments = ["network", "inspect", name]
        elif kind == "image":
            arguments = ["image", "inspect", name]
        else:
            arguments = ["container", "inspect", name]
        result = self._run(arguments, check=False, timeout=20)
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DockerCommandError("Docker inspect returned invalid JSON") from exc
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise DockerCommandError("Docker inspect returned an unexpected shape")
        return payload[0]

    def inspect_container(self, name: str) -> dict | None:
        return self._inspect("container", name)

    def inspect_network(self, name: str) -> dict | None:
        return self._inspect("network", name)

    def inspect_image(self, name: str) -> dict | None:
        return self._inspect("image", name)

    def build_image(
        self, repository: Path, image: str, labels: dict[str, str]
    ) -> None:
        arguments = ["build", "--pull=false"]
        for key, value in sorted(labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.extend(["--tag", image, str(repository)])
        self._run(arguments, timeout=1800)

    def compose_validate(self, document: Path, project: str) -> None:
        self._run(
            ["compose", "--project-name", project, "--file", str(document), "config", "--quiet"],
            timeout=60,
        )

    def compose_up(self, document: Path, project: str) -> None:
        self._run(
            [
                "compose",
                "--project-name",
                project,
                "--file",
                str(document),
                "up",
                "--detach",
                "--no-build",
                "--pull",
                "never",
            ],
            timeout=300,
        )

    def compose_down(self, document: Path, project: str) -> None:
        self._run(
            [
                "compose",
                "--project-name",
                project,
                "--file",
                str(document),
                "down",
                "--timeout",
                "20",
            ],
            timeout=120,
        )

    def wait_healthy(self, names: list[str], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            all_healthy = True
            for name in names:
                container = self.inspect_container(name)
                if container is None:
                    raise DockerCommandError(f"container {name} disappeared during startup")
                state = container.get("State", {})
                if state.get("Status") in {"dead", "exited", "removing"}:
                    raise DockerCommandError(f"container {name} stopped during startup")
                if state.get("Health", {}).get("Status") != "healthy":
                    all_healthy = False
            if all_healthy:
                return
            time.sleep(1)
        raise DockerCommandError("containers did not become healthy before the deadline")
