"""Shared sandbox execution tools for data intelligence engines."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


def record_sandbox_method_calls(
    runtime: EngineRuntimeContext,
    observation: dict[str, Any],
) -> None:
    """Import authoritative Method Hub calls reported by the sandbox broker."""

    for call in observation.get("method_calls", []):
        status = "completed" if call.get("status") == "completed" else "failed"
        runtime.run_context.record_method_call(
            str(call["tool_name"]),
            status=status,
            inputs=dict(call.get("arguments", {})),
            outputs={
                "result": call.get("result"),
                "error": call.get("error"),
                "provider": "sandbox_mcp_broker",
                "command_id": call.get("command_id"),
                "started_at": call.get("started_at"),
                "finished_at": call.get("finished_at"),
            },
            log_refs=(
                [f"sandbox-command://{observation['command_id']}"]
                if observation.get("command_id")
                else []
            ),
        )


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
            record_sandbox_method_calls(runtime, observation)
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
            }
            | {"source_code": code},
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
