"""Request-scoped Docker sandbox for local data analysis."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import subprocess
from typing import Callable, Sequence
from uuid import uuid4

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.runtime.sandbox import EngineSandboxSession

CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class DockerSandboxError(RuntimeError):
    """Raised when a local Docker sandbox operation fails."""


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    """AXIOM-compatible command result returned by the Docker adapter."""

    id: str
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def status(self) -> str:
        return "completed" if self.success else "failed"


class DockerSandbox:
    """Small adapter exposing the sandbox methods used by the SDK runtime."""

    def __init__(
        self,
        *,
        image: str,
        docker_binary: str = "docker",
        memory: str = "1g",
        cpus: str = "1.0",
        pids_limit: int = 128,
        workspace_size: str = "512m",
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        self.image = image
        self.docker_binary = docker_binary
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.workspace_size = workspace_size
        self.command_runner = command_runner
        self.id = ""

    def wait_until_ready(self) -> None:
        """Create and start the isolated request container."""

        if self.id:
            return
        create = self._command(
            [
                "create",
                "--label",
                "data-intelligence.sandbox=true",
                "--network",
                "none",
                "--memory",
                self.memory,
                "--cpus",
                self.cpus,
                "--pids-limit",
                str(self.pids_limit),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=256m",
                "--tmpfs",
                f"/workspace:rw,nosuid,nodev,size={self.workspace_size}",
                "--workdir",
                "/workspace",
                self.image,
                "python",
                "-c",
                "import time; time.sleep(86400)",
            ]
        )
        self.id = create.stdout.decode("utf-8").strip()
        if not self.id:
            raise DockerSandboxError("Docker did not return a container ID.")
        try:
            self._command(["start", self.id])
            self._command(
                ["exec", self.id, "python", "-c", "print('ready')"],
                timeout_seconds=15,
            )
        except Exception:
            self.delete()
            raise

    def write(self, relative_path: str, content: bytes | str) -> None:
        """Copy one file into the request workspace."""

        container_id = self._require_container()
        normalized = _safe_relative_path(relative_path)
        payload = content.encode("utf-8") if isinstance(content, str) else content
        destination = f"/workspace/{normalized.as_posix()}"
        writer = (
            "from pathlib import Path; import sys; "
            "path = Path(sys.argv[1]); "
            "path.parent.mkdir(parents=True, exist_ok=True); "
            "path.write_bytes(sys.stdin.buffer.read())"
        )
        self._command(
            [
                "exec",
                "-i",
                container_id,
                "python",
                "-c",
                writer,
                destination,
            ],
            input_bytes=payload,
            timeout_seconds=30,
        )

    def read(self, relative_path: str) -> bytes:
        """Read one workspace file from the request container."""

        container_id = self._require_container()
        normalized = _safe_relative_path(relative_path)
        result = self._command(
            ["exec", container_id, "cat", f"/workspace/{normalized.as_posix()}"]
        )
        return result.stdout

    def run(
        self,
        source: str,
        *,
        timeout_seconds: int = 120,
        wait: bool = True,
    ) -> DockerCommandResult:
        """Execute Python source in the running request container."""

        del wait
        container_id = self._require_container()
        command_id = str(uuid4())
        try:
            completed = self.command_runner(
                [
                    self.docker_binary,
                    "exec",
                    "-i",
                    "--workdir",
                    "/workspace",
                    container_id,
                    "timeout",
                    "--signal=KILL",
                    f"{timeout_seconds}s",
                    "python",
                    "-",
                ],
                input=source.encode("utf-8"),
                capture_output=True,
                check=False,
                timeout=timeout_seconds + 10,
            )
        except subprocess.TimeoutExpired as exc:
            return DockerCommandResult(
                id=command_id,
                stdout=_decode_output(exc.stdout),
                stderr=(
                    _decode_output(exc.stderr)
                    + f"\nExecution exceeded {timeout_seconds} seconds."
                ).strip(),
                exit_code=124,
            )
        return DockerCommandResult(
            id=command_id,
            stdout=_decode_output(completed.stdout),
            stderr=(
                _decode_output(completed.stderr)
                or (
                    f"Execution exceeded {timeout_seconds} seconds."
                    if completed.returncode == 124
                    else ""
                )
            ),
            exit_code=completed.returncode,
        )

    def delete(self) -> None:
        """Force-remove the request container."""

        if not self.id:
            return
        container_id, self.id = self.id, ""
        try:
            self.command_runner(
                [self.docker_binary, "rm", "-f", container_id],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _command(
        self,
        arguments: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: int = 60,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = self.command_runner(
                [self.docker_binary, *arguments],
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise DockerSandboxError(
                f"Docker executable was not found: {self.docker_binary}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerSandboxError(
                f"Docker command timed out after {timeout_seconds} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = _decode_output(completed.stderr).strip()
            raise DockerSandboxError(
                detail or f"Docker command failed: {' '.join(arguments)}"
            )
        return completed

    def _require_container(self) -> str:
        if not self.id:
            raise DockerSandboxError("The Docker sandbox is not running.")
        return self.id


class DockerSandboxProvider:
    """Provision, stage, and clean up one Docker container per request."""

    def __init__(
        self,
        *,
        image: str,
        docker_binary: str = "docker",
        memory: str = "1g",
        cpus: str = "1.0",
        pids_limit: int = 128,
        workspace_size: str = "512m",
        sandbox_factory: Callable[[], DockerSandbox] | None = None,
    ) -> None:
        self.image = image
        self.docker_binary = docker_binary
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.workspace_size = workspace_size
        self.sandbox_factory = sandbox_factory

    @contextmanager
    def open(self, corpus_package: DataCorpusPackage):
        sandbox = self._create_sandbox()
        try:
            sandbox.wait_until_ready()
            source_paths = _stage_sources(sandbox, corpus_package)
            yield EngineSandboxSession(
                sandbox=sandbox,
                source_paths=source_paths,
            )
        finally:
            sandbox.delete()

    def _create_sandbox(self) -> DockerSandbox:
        if self.sandbox_factory is not None:
            return self.sandbox_factory()
        return DockerSandbox(
            image=self.image,
            docker_binary=self.docker_binary,
            memory=self.memory,
            cpus=self.cpus,
            pids_limit=self.pids_limit,
            workspace_size=self.workspace_size,
        )


def docker_provider_from_env() -> DockerSandboxProvider:
    """Build a local Docker provider from environment settings."""

    raw_pids_limit = os.environ.get("SANDBOX_DOCKER_PIDS_LIMIT", "128")
    try:
        pids_limit = int(raw_pids_limit)
    except ValueError as exc:
        raise ValueError("SANDBOX_DOCKER_PIDS_LIMIT must be an integer.") from exc
    if pids_limit <= 0:
        raise ValueError("SANDBOX_DOCKER_PIDS_LIMIT must be positive.")
    return DockerSandboxProvider(
        image=os.environ.get(
            "SANDBOX_DOCKER_IMAGE",
            "data-intelligence-sandbox:local",
        ),
        docker_binary=os.environ.get("SANDBOX_DOCKER_BINARY", "docker"),
        memory=os.environ.get("SANDBOX_DOCKER_MEMORY", "1g"),
        cpus=os.environ.get("SANDBOX_DOCKER_CPUS", "1.0"),
        pids_limit=pids_limit,
        workspace_size=os.environ.get("SANDBOX_DOCKER_WORKSPACE_SIZE", "512m"),
    )


def _stage_sources(
    sandbox: DockerSandbox,
    corpus_package: DataCorpusPackage,
) -> dict[str, str]:
    source_paths: dict[str, str] = {}
    used_names: set[str] = set()
    for index, source in enumerate(corpus_package.sources):
        source_text = str(source)
        host_path = Path(source_text)
        if not host_path.is_file():
            raise ValueError(
                "The Docker sandbox requires local source files: " f"{source_text}"
            )
        filename = host_path.name
        if filename in used_names:
            filename = f"{index}_{filename}"
        used_names.add(filename)
        relative_path = f"input/{filename}"
        sandbox.write(relative_path, host_path.read_bytes())
        source_paths[source_text] = f"/workspace/{relative_path}"
    return source_paths


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Sandbox file paths must be relative and cannot traverse.")
    return path


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
