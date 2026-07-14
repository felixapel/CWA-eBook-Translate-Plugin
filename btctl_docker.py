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

    def prepare_data_directory(self, image: str, path: Path) -> None:
        """Make one bind-mounted data directory private and writable by uid 101."""
        self._run(
            [
                "run",
                "--rm",
                "--user",
                "0:0",
                "--entrypoint",
                "/bin/sh",
                "--mount",
                f"type=bind,src={path},dst=/data",
                image,
                "-ec",
                "chown 101:102 /data; chmod 0700 /data; "
                "test \"$(stat -c %u:%g /data)\" = 101:102",
            ],
            timeout=60,
        )

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

    def create_network(
        self, name: str, labels: dict[str, str], *, internal: bool
    ) -> None:
        arguments = ["network", "create", "--driver", "bridge"]
        if internal:
            arguments.append("--internal")
        for key, value in sorted(labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.append(name)
        self._run(arguments, timeout=60)

    def create_container(self, spec) -> None:
        arguments = [
            "create",
            "--name",
            spec.name,
            "--network",
            spec.primary_network,
            "--env-file",
            str(spec.env_file),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--restart",
            "unless-stopped",
        ]
        if spec.network_alias:
            arguments.extend(["--network-alias", spec.network_alias])
        if spec.role == "api":
            arguments.extend(
                [
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m,uid=101,gid=102,mode=700",
                    "--pids-limit",
                    "256",
                    "--memory",
                    "1g",
                    "--cpus",
                    "2",
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "--mount",
                    f"type=bind,src={spec.data_dir},dst=/app/data",
                ]
            )
        else:
            arguments.extend(
                [
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m,uid=101,gid=102,mode=700",
                    "--pids-limit",
                    "64",
                    "--memory",
                    "128m",
                    "--cpus",
                    "0.5",
                ]
            )
        if spec.publish_port is not None:
            arguments.extend(["--publish", f"{spec.publish_port}:8080/tcp"])
        for key, value in sorted(spec.labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.append(spec.image)
        self._run(arguments, timeout=120)

    def connect_network(self, network: str, container: str) -> None:
        self._run(["network", "connect", network, container], timeout=60)

    def start_container(self, name: str) -> None:
        self._run(["start", name], timeout=60)

    def stop_container(self, name: str) -> None:
        self._run(["stop", "--time", "30", name], timeout=60)

    def remove_container(self, name: str) -> None:
        self._run(["rm", "--force", name], timeout=60)

    def remove_network(self, name: str) -> None:
        self._run(["network", "rm", name], timeout=60)
