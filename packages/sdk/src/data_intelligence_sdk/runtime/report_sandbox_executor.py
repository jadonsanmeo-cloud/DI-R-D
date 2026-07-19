"""Adapter from the report engine sandbox contract to a request sandbox session."""

from __future__ import annotations

import json
import re
from typing import Any

from data_intelligence_sdk.core.types import InterfaceDefinition
from data_intelligence_sdk.runtime.deep_agent_sandbox import DeepAgentSandboxSession
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession
from data_intelligence_sdk.sandbox.executor import SandboxRunResult

_PYTHON_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RequestSandboxExecutor:
    """Execute generated ReportEngine interfaces in the active request sandbox."""

    def __init__(
        self,
        session: DeepAgentSandboxSession,
        run_artifact: RunArtifactSession | None,
    ) -> None:
        self.session = session
        self.run_artifact = run_artifact

    def run(
        self,
        interface: InterfaceDefinition,
        inputs: dict[str, Any],
        resource_policy: dict[str, Any] | None = None,
    ) -> SandboxRunResult:
        return self._execute(interface, inputs, resource_policy, mode="run")

    def validate(
        self,
        interface: InterfaceDefinition,
        validation_inputs: dict[str, Any],
        resource_policy: dict[str, Any] | None = None,
    ) -> SandboxRunResult:
        return self._execute(
            interface,
            validation_inputs,
            resource_policy,
            mode="validate",
        )

    def _execute(
        self,
        interface: InterfaceDefinition,
        inputs: dict[str, Any],
        resource_policy: dict[str, Any] | None,
        *,
        mode: str,
    ) -> SandboxRunResult:
        if self.run_artifact is None:
            return SandboxRunResult(
                status="failed",
                error="Generated report code requires an active run artifact.",
            )
        if not _PYTHON_IDENTIFIER.fullmatch(interface.name):
            return SandboxRunResult(
                status="failed",
                error=f"Invalid generated Python function name: {interface.name!r}.",
            )

        source_code = str(
            interface.metadata.get("source_code") or interface.implementation_ref or ""
        )
        if not source_code.strip():
            return SandboxRunResult(
                status="failed",
                error="Generated interface does not contain Python source code.",
            )

        translated_inputs = self._translate_paths(inputs)
        translated_source = self._translate_source_paths(source_code)
        call_source = self._call_source(
            translated_source,
            interface.name,
            translated_inputs,
        )
        timeout_seconds = self._timeout_seconds(resource_policy)
        try:
            observation = self.session.execute_python(
                call_source,
                self.run_artifact,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return SandboxRunResult(
                status="failed",
                error=str(exc),
                metadata={"mode": mode},
            )

        success = bool(observation.get("success"))
        artifact_refs = [
            str(ref)
            for ref in (
                observation.get("code_artifact_ref"),
                observation.get("execution_artifact_ref"),
            )
            if ref
        ]
        error = None
        if not success:
            error = str(
                observation.get("stderr")
                or observation.get("status")
                or "Generated report code failed."
            )
        return SandboxRunResult(
            status="completed" if success else "failed",
            result=observation.get("result"),
            error=error,
            artifact_refs=artifact_refs,
            metadata={
                "mode": mode,
                "sandbox_id": observation.get("sandbox_id"),
                "command_id": observation.get("command_id"),
                "exit_code": observation.get("exit_code"),
            },
        )

    def _translate_paths(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.session.source_paths.get(value, value)
        if isinstance(value, dict):
            return {key: self._translate_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._translate_paths(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._translate_paths(item) for item in value)
        return value

    def _translate_source_paths(self, source_code: str) -> str:
        translated = source_code
        for host_path, sandbox_path in self.session.source_paths.items():
            translated = translated.replace(
                json.dumps(host_path),
                json.dumps(sandbox_path),
            )
            translated = translated.replace(repr(host_path), repr(sandbox_path))
        return translated

    @staticmethod
    def _call_source(
        source_code: str,
        function_name: str,
        inputs: dict[str, Any],
    ) -> str:
        name_literal = json.dumps(function_name)
        inputs_literal = json.dumps(inputs, ensure_ascii=True, default=str)
        return (
            f"{source_code.rstrip()}\n\n"
            f"__report_tool = globals().get({name_literal})\n"
            "if callable(__report_tool):\n"
            f"    result = __report_tool(**{inputs_literal})\n"
            "elif 'result' not in globals():\n"
            f"    raise RuntimeError('Generated source did not define {function_name}')\n"
        )

    @staticmethod
    def _timeout_seconds(resource_policy: dict[str, Any] | None) -> int:
        if not resource_policy:
            return 120
        value = resource_policy.get("timeout_seconds", 120)
        try:
            return max(1, min(int(value), 300))
        except (TypeError, ValueError):
            return 120
