"""Request-scoped sandbox services shared by all engines."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession

RESULT_MARKER = "__AXIOM_RESULT__"
MAX_COMMAND_LENGTH = 262_144


@dataclass(frozen=True, slots=True)
class SandboxEnvironment:
    """Capabilities and access policy of one request-scoped sandbox."""

    runtime: str = "python"
    dependency_management: str = "axiom_sandbox_service"
    network_access: bool = False
    source_access: str = "read_only"

    def to_prompt_payload(self) -> dict[str, Any]:
        """Return the serializable environment contract exposed to agents."""

        return {
            "runtime": self.runtime,
            "dependency_management": self.dependency_management,
            "network_access": self.network_access,
            "source_access": self.source_access,
        }


class SandboxSessionProvider(Protocol):
    """Creates one shared sandbox session for one pipeline request."""

    def open(
        self,
        corpus_package: DataCorpusPackage,
    ) -> AbstractContextManager["EngineSandboxSession"]:
        """Open a staged request sandbox and clean it up on exit."""


@dataclass(slots=True)
class EngineSandboxSession:
    """One staged AXIOM sandbox shared by all engines in a request."""

    sandbox: object
    source_paths: dict[str, str] = field(default_factory=dict)
    environment: SandboxEnvironment = field(default_factory=SandboxEnvironment)

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
