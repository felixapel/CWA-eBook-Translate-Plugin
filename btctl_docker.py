"""Small, shell-free Docker CLI adapter used by btctl lifecycle code."""

from __future__ import annotations

import json
import os
import re
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
            # A missing object is normal lifecycle state. Every other failure
            # (daemon outage, permissions, context/TLS failure) is unknown
            # state and must never be flattened into "absent".
            detail = result.stderr.strip().casefold()
            object_name = name.casefold()
            missing_suffixes = {
                "container": (f"no such container: {object_name}",),
                "image": (f"no such image: {object_name}",),
                "network": (
                    f"network {object_name} not found",
                    f"no such network: {object_name}",
                ),
            }[kind]
            if any(
                line.strip().endswith(missing_suffixes)
                for line in detail.splitlines()
            ):
                return None
            raise DockerCommandError(f"Docker {kind} inspect failed")
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
        revision = labels.get("io.cwa-translate.revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise DockerCommandError("image build requires one exact source revision")
        try:
            archive = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "archive",
                    "--format=tar",
                    revision,
                ],
                check=False,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerCommandError("Git archive could not be executed") from exc
        if archive.returncode != 0:
            raise DockerCommandError("Git archive failed")
        arguments = ["build", "--pull=false"]
        for key, value in sorted(labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.extend(["--tag", image, "-"])
        try:
            result = subprocess.run(
                ["docker", *arguments],
                input=archive.stdout,
                check=False,
                capture_output=True,
                timeout=1800,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerCommandError("Docker build could not be executed") from exc
        if result.returncode != 0:
            raise DockerCommandError("Docker build failed")

    def prepare_data_directory(self, image: str, path: Path) -> None:
        """Give uid 101 ownership while retaining private operator read access."""
        operator_gid = os.getgid()
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
                "test -z \"$(find /data -xdev ! -type d ! -type f -print)\"; "
                f"find /data -xdev -type d -exec chown 101:{operator_gid} {{}} \\;; "
                f"find /data -xdev -type f -exec chown 101:{operator_gid} {{}} \\;; "
                "find /data -xdev -type d -exec chmod 2750 {} \\;; "
                "find /data -xdev -type f -exec chmod 0640 {} \\;; "
                f"test \"$(stat -c %u:%g /data)\" = 101:{operator_gid}; "
                "test \"$(stat -c %a /data)\" = 2750",
            ],
            timeout=60,
        )

    def prepare_migration_source(self, image: str, path: Path) -> None:
        """Grant the invoking operator private checkpoint access to stopped v2.1.4 data."""
        operator_gid = os.getgid()
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
                "test -z \"$(find /data -xdev ! -type d ! -type f -print)\"; "
                f"find /data -xdev -type d -exec chgrp {operator_gid} {{}} \\;; "
                f"find /data -xdev -type f -exec chgrp {operator_gid} {{}} \\;; "
                "find /data -xdev -type d -exec chmod 2770 {} \\;; "
                "find /data -xdev -type f -exec chmod 0660 {} \\;; "
                f"test \"$(stat -c %g /data)\" = {operator_gid}; "
                "test \"$(stat -c %a /data)\" = 2770",
            ],
            timeout=60,
        )

    def probe_http(self, container: str, url: str) -> None:
        """Prove one exact runtime container can reach a non-5xx endpoint."""
        script = (
            "import sys,urllib.error,urllib.request;"
            "NoRedirect=type('NoRedirect',(urllib.request.HTTPRedirectHandler,),"
            "{'redirect_request':lambda self,*args,**kwargs:None});"
            "opener=urllib.request.build_opener(NoRedirect);code=0;"
            "\ntry:\n r=opener.open(sys.argv[1],timeout=5);code=r.status;r.close()"
            "\nexcept urllib.error.HTTPError as exc:\n code=exc.code"
            "\nexcept Exception:\n sys.exit(2)"
            "\nsys.exit(0 if 100 <= code < 500 else 3)"
        )
        self._run(
            ["exec", container, "python", "-c", script, url],
            timeout=15,
        )

    def probe_auth(self, container: str, url: str) -> None:
        """Prove an auth authority fails closed without credentials."""
        script = (
            "import sys,urllib.error,urllib.request;"
            "NoRedirect=type('NoRedirect',(urllib.request.HTTPRedirectHandler,),"
            "{'redirect_request':lambda self,*args,**kwargs:None});"
            "opener=urllib.request.build_opener(NoRedirect);code=0;"
            "\ntry:\n r=opener.open(sys.argv[1],timeout=5);code=r.status;r.close()"
            "\nexcept urllib.error.HTTPError as exc:\n code=exc.code"
            "\nexcept Exception:\n sys.exit(2)"
            "\nsys.exit(0 if code in (401,403) or 300 <= code < 400 else 3)"
        )
        self._run(
            ["exec", container, "python", "-c", script, url],
            timeout=15,
        )

    def probe_image_version(self, image_id: str, expected_version: str) -> None:
        """Read VERSION from one immutable, network-isolated image ID."""
        script = (
            "from pathlib import Path;import sys;"
            "value=Path('/app/VERSION').read_text(encoding='utf-8').strip();"
            "sys.exit(0 if value == sys.argv[1] else 3)"
        )
        self._run(
            [
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--entrypoint",
                "python",
                image_id,
                "-c",
                script,
                expected_version,
            ],
            timeout=30,
        )

    def probe_sqlite(self, container: str, database_path: str) -> None:
        """Open and quick-check the cache as the unprivileged runtime user."""
        script = (
            "import sqlite3,sys;"
            "\ntry:\n"
            " db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True,timeout=5);"
            " db.execute('PRAGMA query_only=ON');"
            " row=db.execute('PRAGMA quick_check').fetchone();db.close()"
            "\nexcept Exception:\n sys.exit(2)"
            "\nsys.exit(0 if row == ('ok',) else 3)"
        )
        self._run(
            ["exec", container, "python", "-c", script, database_path],
            timeout=30,
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
            "--user",
            "101:102",
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
