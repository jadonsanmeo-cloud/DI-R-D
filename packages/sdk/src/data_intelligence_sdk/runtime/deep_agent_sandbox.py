"""Request-scoped sandbox support for the general Deep Agent."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol

from deepagents.backends.protocol import (
    BackendProtocol,
    FileData,
    ReadResult,
    WriteResult,
)

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession

RESULT_MARKER = "__AXIOM_RESULT__"
WORKSPACE_ROOT = PurePosixPath("/workspace")
AGENT_ROOT = WORKSPACE_ROOT / "agent"
MAX_COMMAND_LENGTH = 262_144


class SandboxSessionProvider(Protocol):
    """Creates one sandbox session for one pipeline request."""

    def open(
        self,
        corpus_package: DataCorpusPackage,
    ) -> AbstractContextManager["DeepAgentSandboxSession"]:
        """Open a staged request sandbox and clean it up on exit."""


class DeepAgentSandboxBackend(BackendProtocol):
    """Expose AXIOM sandbox files through the Deep Agents backend contract."""

    def __init__(self, sandbox: object) -> None:
        self.sandbox = sandbox

    def workspace_path(self, file_path: str) -> str:
        """Convert a virtual workspace path to an AXIOM-relative path."""

        path = self._absolute_path(file_path)
        try:
            relative = path.relative_to(WORKSPACE_ROOT)
        except ValueError as exc:
            raise ValueError("Paths must be located under /workspace.") from exc
        if not relative.parts:
            raise ValueError("A file path below /workspace is required.")
        return relative.as_posix()

    def generated_path(self, file_path: str) -> str:
        """Return an AXIOM-relative path restricted to generated agent files."""

        path = self._absolute_path(file_path)
        try:
            path.relative_to(AGENT_ROOT)
        except ValueError as exc:
            raise ValueError(
                "Generated files must be located under /workspace/agent."
            ) from exc
        return self.workspace_path(file_path)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            relative_path = self.generated_path(file_path)
            self.sandbox.write(relative_path, content)
        except Exception as exc:
            return WriteResult(error=f"Error writing file '{file_path}': {exc}")
        return WriteResult(path=file_path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        try:
            content = self.sandbox.read(self.workspace_path(file_path)).decode("utf-8")
            lines = content.splitlines(keepends=True)
            if offset >= len(lines) and lines:
                return ReadResult(
                    error=(
                        f"Line offset {offset} exceeds file length "
                        f"({len(lines)} lines)"
                    )
                )
            window = "".join(lines[offset : offset + limit])
        except Exception as exc:
            return ReadResult(error=f"Error reading file '{file_path}': {exc}")
        return ReadResult(file_data=FileData(content=window, encoding="utf-8"))

    @staticmethod
    def _absolute_path(file_path: str) -> PurePosixPath:
        path = PurePosixPath(file_path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Sandbox paths must be absolute and cannot traverse parents."
            )
        return path


@dataclass(slots=True)
class DeepAgentSandboxSession:
    """One staged AXIOM sandbox shared by all steps in an engine run."""

    sandbox: object
    source_paths: dict[str, str] = field(default_factory=dict)
    backend: DeepAgentSandboxBackend = field(init=False)

    def __post_init__(self) -> None:
        self.backend = DeepAgentSandboxBackend(self.sandbox)

    def execute_python(
        self,
        code: str,
        run_artifact: RunArtifactSession,
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Persist and execute one generated Python source attempt."""

        if not code.strip():
            raise ValueError("Generated Python source cannot be empty.")
        runner = self._runner_source(code)
        if len(runner) > MAX_COMMAND_LENGTH:
            raise ValueError(
                f"Generated Python command exceeds {MAX_COMMAND_LENGTH} characters."
            )
        attempt = run_artifact.record_code_attempt(code)
        command = self.sandbox.run(
            runner,
            timeout_seconds=timeout_seconds,
            wait=True,
        )
        stdout = str(getattr(command, "stdout", "") or "")
        stderr = str(getattr(command, "stderr", "") or "")
        success = bool(getattr(command, "success", False))
        observation = {
            "success": success,
            "status": self._status_text(getattr(command, "status", None)),
            "result": self._parse_result(stdout) if success else None,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": getattr(command, "exit_code", None),
            "sandbox_id": str(getattr(self.sandbox, "id", "")),
            "command_id": str(getattr(command, "id", "")),
            "attempt": attempt.attempt,
            "code_artifact_ref": attempt.artifact_ref,
            "execution_artifact_ref": run_artifact.execution_ref(attempt),
            "method_calls": [
                (
                    call.model_dump(mode="json")
                    if hasattr(call, "model_dump")
                    else dict(call)
                )
                for call in getattr(command, "method_calls", [])
            ],
        }
        run_artifact.record_execution(attempt, observation)
        return observation

    @staticmethod
    def _runner_source(code: str) -> str:
        source_literal = json.dumps(code, ensure_ascii=True)
        return (
            "import json as __axiom_json\n"
            f"__axiom_source = {source_literal}\n"
            "__axiom_namespace = {'__name__': '__axiom_generated__'}\n"
            "exec(compile(__axiom_source, '<generated-analysis>', 'exec'), "
            "__axiom_namespace)\n"
            "__axiom_result = __axiom_namespace.get('result')\n"
            f"print({RESULT_MARKER!r} + __axiom_json.dumps(__axiom_result, default=str))\n"
        )

    @staticmethod
    def _parse_result(stdout: str) -> Any:
        for line in reversed(stdout.splitlines()):
            if line.startswith(RESULT_MARKER):
                return json.loads(line.removeprefix(RESULT_MARKER))
        return None

    @staticmethod
    def _status_text(status: object) -> str:
        value = getattr(status, "value", status)
        return str(value or "unknown")
