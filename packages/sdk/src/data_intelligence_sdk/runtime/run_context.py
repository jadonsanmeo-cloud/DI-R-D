"""Runtime context passed to engines so they can record structured trace."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from data_intelligence_sdk.core.types import (
    EngineOutput,
    EngineStep,
    EngineTrace,
    EvidenceBundle,
    MethodCall,
    TraceStatus,
)

EventRecorder = Callable[..., dict[str, Any]]


class EngineRunContext:
    """Collects structured execution trace while an engine runs.

    Engines should use this context to record steps, Method Hub calls, artifact
    references, and log references instead of inventing a trace format.
    """

    def __init__(
        self,
        event_recorder: EventRecorder | None = None,
    ) -> None:
        self.trace = EngineTrace()
        self._event_recorder = event_recorder

    def _record_event(
        self,
        *,
        phase: str,
        event_type: str,
        status: TraceStatus,
        payload: dict[str, Any],
    ) -> None:
        if self._event_recorder is not None:
            self._event_recorder(
                phase=phase,
                event_type=event_type,
                status=status,
                payload=payload,
            )

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
        plan = step.outputs.get("plan")
        if plan is not None:
            self._record_event(
                phase="planning",
                event_type="plan.created",
                status=status,
                payload={"step_name": name, "plan": plan},
            )
        self._record_event(
            phase="engine",
            event_type="engine.step",
            status=status,
            payload=asdict(step),
        )
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
        self._record_event(
            phase="tool",
            event_type="tool.called",
            status=status,
            payload=asdict(method_call),
        )
        return method_call

    def add_artifact_ref(self, artifact_ref: str) -> None:
        self.trace.artifact_refs.append(artifact_ref)

    def add_log_ref(self, log_ref: str) -> None:
        self.trace.log_refs.append(log_ref)

    def build_output(
        self,
        *,
        engine_name: str,
        answer: str | None = None,
        result: Any = None,
        evidence: EvidenceBundle | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EngineOutput:
        return EngineOutput(
            engine_name=engine_name,
            answer=answer,
            result=result,
            evidence=evidence,
            trace=self.trace,
            metadata=metadata or {},
        )
