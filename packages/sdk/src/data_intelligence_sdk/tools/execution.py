"""Shared sandbox execution tools for data intelligence engines."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


def create_execute_python_tool(runtime: EngineRuntimeContext) -> BaseTool:
    """Create a request-scoped Python sandbox execution tool."""

    @tool
    def execute_python(code: str) -> dict[str, Any]:
        """Persist and execute one complete Python analysis program."""

        if runtime.sandbox is None or runtime.run_artifact is None:
            raise RuntimeError("The request sandbox or run artifact is unavailable.")
        try:
            observation = runtime.sandbox.execute_python(
                code,
                runtime.run_artifact,
            )
        except ValueError as exc:
            observation = {
                "success": False,
                "status": "rejected",
                "result": None,
                "stdout": "",
                "stderr": str(exc),
                "exit_code": None,
            }
        artifact_refs = [
            str(observation[key])
            for key in ("code_artifact_ref", "execution_artifact_ref")
            if observation.get(key)
        ]
        runtime.run_context.record_method_call(
            "execute_python",
            status="completed" if observation.get("success") else "failed",
            inputs={
                key: observation[key]
                for key in ("attempt", "code_artifact_ref")
                if observation.get(key) is not None
            },
            outputs=observation,
            artifact_refs=artifact_refs,
            log_refs=(
                [f"sandbox-command://{observation['command_id']}"]
                if observation.get("command_id")
                else []
            ),
        )
        return observation

    return execute_python
