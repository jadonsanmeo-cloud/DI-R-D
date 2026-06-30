"""Runtime context passed to engines so they can record structured trace."""

from __future__ import annotations

from typing import Any

from data_intelligence_sdk.core.types import EngineOutput, EngineStep, EngineTrace, MethodCall, TraceStatus


class EngineRunContext:
    """Collects structured execution trace while an engine runs.

    Engines should use this context to record steps, Method Hub calls, artifact
    references, and log references instead of inventing a trace format.
    """

    def __init__(self) -> None:
        self.trace = EngineTrace()

    def record_step(
        self,
        name: str,
        *,
        status: TraceStatus = "completed",
        description: str | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        log_refs: list[str] | None = None,
    ) -> EngineStep:
        step = EngineStep(
            name=name,
            status=status,
            description=description,
            inputs=inputs or {},
            outputs=outputs or {},
            artifact_refs=artifact_refs or [],
            log_refs=log_refs or [],
        )
        self.trace.steps.append(step)
        return step

    def record_method_call(
        self,
        method_name: str,
        *,
        status: TraceStatus = "completed",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        log_refs: list[str] | None = None,
    ) -> MethodCall:
        method_call = MethodCall(
            method_name=method_name,
            status=status,
            inputs=inputs or {},
            outputs=outputs or {},
            artifact_refs=artifact_refs or [],
            log_refs=log_refs or [],
        )
        self.trace.method_calls.append(method_call)
        return method_call

    def add_artifact_ref(self, artifact_ref: str) -> None:
        self.trace.artifact_refs.append(artifact_ref)

    def add_log_ref(self, log_ref: str) -> None:
        self.trace.log_refs.append(log_ref)

    def build_output(
        self,
        *,
        engine_name: str,
        result: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> EngineOutput:
        return EngineOutput(
            engine_name=engine_name,
            result=result,
            trace=self.trace,
            metadata=metadata or {},
        )
