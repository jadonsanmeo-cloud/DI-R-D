"""High-level orchestration boundary for the architecture flow."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from typing import Any

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    FinalResponse,
    PreparedExecution,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.intent import IntentAnalysis
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.sandbox import SandboxSessionProvider
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient, MCPToolDefinition
from data_intelligence_sdk.runtime.event_payload import runtime_event_payload
from data_intelligence_sdk.runtime.report_sandbox_executor import (
    RequestSandboxExecutor,
)
from data_intelligence_sdk.runtime.run_context import EngineRunContext
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession
from data_intelligence_sdk.spec.default_confirmation import SpecConfirmationDecision


class DataIntelligencePipeline:
    """Coordinates the major platform components.

    The base version only defines the composition point. Concrete behavior will
    be added as the SDK boundaries become clearer.
    """

    def __init__(
        self,
        *,
        intent_analyzer: object,
        spec_builder: object,
        spec_confirmation: object,
        engine_registry: object,
        mcp_client: MCPMethodClient | None = None,
        mcp_tools: tuple[MCPToolDefinition, ...] = (),
        interface_registry: object | None = None,
        interface_builder: object | None = None,
        sandbox_executor: object | None = None,
        artifact_store: object | None = None,
        log_store: object | None = None,
        resource_manager: object | None = None,
        sandbox_provider: SandboxSessionProvider | None = None,
        include_evidence: bool = True,
        logger: RuntimeLogger | None = None,
        max_spec_revision_rounds: int = 3,
    ) -> None:
        self.intent_analyzer = intent_analyzer
        self.spec_builder = spec_builder
        self.spec_confirmation = spec_confirmation
        self.engine_registry = engine_registry
        self.mcp_client = mcp_client
        self.mcp_tools = mcp_tools
        self.interface_registry = interface_registry
        self.interface_builder = interface_builder
        self.sandbox_executor = sandbox_executor
        self.artifact_store = artifact_store
        self.log_store = log_store
        self.resource_manager = resource_manager
        self.sandbox_provider = sandbox_provider
        self.include_evidence = include_evidence
        self.logger = logger
        self.max_spec_revision_rounds = max_spec_revision_rounds

    def _log(self, event: str, payload: dict[str, object] | None = None) -> None:
        if self.logger is not None:
            self.logger.log(event, payload or {})

    @staticmethod
    def _record_artifact_event(
        run_artifact: RunArtifactSession | None,
        *,
        phase: str,
        event_type: str,
        status: str = "completed",
        payload: dict[str, Any] | None = None,
    ) -> None:
        if run_artifact is not None:
            run_artifact.record_event(
                phase=phase,
                event_type=event_type,
                status=status,
                payload=payload,
            )

    def _record_runtime_event(
        self,
        run_artifact: RunArtifactSession | None,
        *,
        phase: str,
        event_type: str,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = (
            run_artifact.record_event(
                phase=phase,
                event_type=event_type,
                status=status,
                payload=payload,
            )
            if run_artifact is not None
            else {
                "event_id": None,
                "run_id": None,
                "sequence": None,
                "phase": phase,
                "event_type": event_type,
                "status": status,
                "payload": payload,
            }
        )
        self._log(
            "pipeline.runtime_event",
            runtime_event_payload(event),
        )
        return event

    def run(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> FinalResponse:
        """Run the full data intelligence flow."""

        prepared = self.prepare_spec(
            query,
            corpus_package,
            session_context,
            user_context,
        )
        try:
            confirmed_spec = self._confirm_spec(prepared)
        except Exception as exc:
            if prepared.run_artifact is not None:
                prepared.run_artifact.finalize(
                    status="failed",
                    failure_phase="spec_confirmation",
                    error=_artifact_error(exc),
                )
            raise
        return self.execute_confirmed_spec(prepared, confirmed_spec)

    def prepare_spec(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> PreparedExecution:
        """Analyze intent and build a draft spec without selecting an engine."""

        create_run = getattr(self.artifact_store, "create_run", None)
        run_artifact = (
            create_run(query, corpus_package) if callable(create_run) else None
        )
        try:
            self._log(
                "pipeline.start",
                {
                    "query": query.text,
                    "source_count": len(corpus_package.sources),
                    "has_schema": bool(corpus_package.schemas),
                    "has_metadata": bool(corpus_package.metadata),
                    "artifact_ref": (
                        run_artifact.artifact_ref if run_artifact is not None else None
                    ),
                },
            )
            analyze_details = getattr(self.intent_analyzer, "analyze_details", None)
            analyzed_intent = (
                analyze_details(
                    query, corpus_package, session_context, user_context
                )
                if callable(analyze_details)
                else self.intent_analyzer.analyze(
                    query, corpus_package, session_context, user_context
                )
            )
            if isinstance(analyzed_intent, IntentAnalysis):
                intent = analyzed_intent.intent
                intent_analysis = analyzed_intent
                intent_payload = analyzed_intent.event_payload()
            else:
                intent = analyzed_intent
                intent_analysis = None
                intent_payload = {"intent": intent, "source": "local"}
            self._log("pipeline.intent_analyzed", intent_payload)
            self._record_artifact_event(
                run_artifact,
                phase="intent",
                event_type="intent.analyzed",
                payload=intent_payload,
            )
            build_with_intent_analysis = getattr(
                self.spec_builder,
                "build_with_intent_analysis",
                None,
            )
            if intent_analysis is not None and callable(build_with_intent_analysis):
                spec = build_with_intent_analysis(
                    query,
                    intent_analysis,
                    corpus_package,
                    session_context,
                    user_context,
                )
            else:
                spec = self.spec_builder.build(
                    query, intent, corpus_package, session_context, user_context
                )
            self._log(
                "pipeline.spec_built",
                {
                    "intent": spec.intent,
                    "objective": spec.objective,
                    "capability_count": len(spec.capability_requirements),
                    "data_requirement_count": len(spec.data_requirements),
                },
            )
            self._record_artifact_event(
                run_artifact,
                phase="spec_builder",
                event_type="spec.built",
                payload=asdict(spec),
            )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    failure_phase="spec_preparation",
                    error=_artifact_error(exc),
                )
            raise
        return PreparedExecution(
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            spec=spec,
            session_context=session_context,
            user_context=user_context,
            intent_analysis=intent_analysis,
            run_artifact=run_artifact,
            run_artifact_id=(run_artifact.run_id if run_artifact is not None else None),
        )

    def revise_spec(
        self,
        prepared: PreparedExecution,
        previous_spec: ExecutionSpec,
        feedback: str,
    ) -> ExecutionSpec:
        """Revise a prepared execution spec from explicit user feedback."""

        if not feedback.strip():
            raise ValueError("Spec revision requires feedback.")
        run_artifact = self._resolve_run_artifact(prepared)
        self._record_artifact_event(
            run_artifact,
            phase="spec_builder",
            event_type="spec.revision_requested",
            payload={
                "feedback": feedback,
                "previous_spec": asdict(previous_spec),
            },
        )
        revise_with_intent_analysis = getattr(
            self.spec_builder,
            "revise_with_intent_analysis",
            None,
        )
        revise = getattr(self.spec_builder, "revise", None)
        if revise is None:
            raise TypeError(
                "Spec confirmation requested revision, but spec_builder has no revise method."
            )
        self._log(
            "pipeline.spec_revision_started",
            {"intent": prepared.intent, "feedback": feedback},
        )
        if prepared.intent_analysis is not None and callable(
            revise_with_intent_analysis
        ):
            revised = revise_with_intent_analysis(
                previous_spec=previous_spec,
                user_feedback=feedback,
                query=prepared.query,
                intent_analysis=prepared.intent_analysis,
                corpus_package=prepared.corpus_package,
                session_context=prepared.session_context,
                user_context=prepared.user_context,
            )
        else:
            revised = revise(
                previous_spec=previous_spec,
                user_feedback=feedback,
                query=prepared.query,
                intent=prepared.intent,
                corpus_package=prepared.corpus_package,
                session_context=prepared.session_context,
                user_context=prepared.user_context,
            )
        self._log(
            "pipeline.spec_revised",
            {
                "intent": revised.intent,
                "objective": revised.objective,
                "capability_count": len(revised.capability_requirements),
                "data_requirement_count": len(revised.data_requirements),
            },
        )
        self._record_artifact_event(
            run_artifact,
            phase="spec_builder",
            event_type="spec.revised",
            payload=asdict(revised),
        )
        return revised

    def execute_confirmed_spec(
        self,
        prepared: PreparedExecution,
        confirmed_spec: ExecutionSpec,
    ) -> FinalResponse:
        """Execute a previously prepared and explicitly confirmed spec."""

        if not confirmed_spec.confirmed:
            raise ValueError(
                "Execution spec must be confirmed before engine selection."
            )
        run_artifact = self._resolve_run_artifact(prepared)
        self._record_artifact_event(
            run_artifact,
            phase="spec_builder",
            event_type="spec.confirmed",
            payload=asdict(confirmed_spec),
        )
        self._log(
            "pipeline.spec_confirmed",
            {
                "confirmed": confirmed_spec.confirmed,
                "engine_hint": confirmed_spec.engine_hint,
            },
        )
        phase = "engine_selection"
        engine = None
        try:
            engine = self.engine_registry.select(confirmed_spec)
            self._record_artifact_event(
                run_artifact,
                phase="engine_selection",
                event_type="engine.selected",
                payload={
                    "engine_name": getattr(
                        engine,
                        "name",
                        type(engine).__name__,
                    )
                },
            )
            self._log(
                "pipeline.engine_selected",
                {"engine_name": getattr(engine, "name", type(engine).__name__)},
            )
            phase = "sandbox_provisioning"
            sandbox_context = (
                self.sandbox_provider.open(prepared.corpus_package)
                if self.sandbox_provider is not None
                else nullcontext(None)
            )
            with sandbox_context as sandbox:
                phase = "engine_execution"
                sandbox_executor = self.sandbox_executor
                if sandbox_executor is None and sandbox is not None:
                    sandbox_executor = RequestSandboxExecutor(sandbox, run_artifact)

                def record_runtime_event(**event: Any) -> dict[str, Any]:
                    return self._record_runtime_event(run_artifact, **event)

                runtime = EngineRuntimeContext(
                    run_context=EngineRunContext(
                        event_recorder=record_runtime_event,
                    ),
                    mcp_client=self.mcp_client,
                    mcp_tools=self.mcp_tools,
                    interface_registry=self.interface_registry,
                    interface_builder=self.interface_builder,
                    sandbox_executor=sandbox_executor,
                    artifact_store=self.artifact_store,
                    log_store=self.log_store,
                    resource_manager=self.resource_manager,
                    sandbox=sandbox,
                    run_artifact=run_artifact,
                )
                output = engine.run(
                    confirmed_spec,
                    prepared.corpus_package,
                    runtime,
                    prepared.user_context,
                )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    engine_name=(
                        getattr(engine, "name", type(engine).__name__)
                        if engine is not None
                        else None
                    ),
                    failure_phase=phase,
                    error=_artifact_error(exc),
                )
            raise
        self._log(
            "pipeline.engine_completed",
            {
                "engine_name": output.engine_name,
                "step_count": len(output.trace.steps),
                "method_call_count": len(output.trace.method_calls),
            },
        )
        response = _final_response_from_engine_output(
            output,
            include_evidence=self.include_evidence,
        )

        artifact_ref = run_artifact.artifact_ref if run_artifact is not None else None
        if artifact_ref is not None:
            response.metadata["artifact_ref"] = artifact_ref
        self._record_artifact_event(
            run_artifact,
            phase="response",
            event_type="response.completed",
            payload=asdict(response),
        )
        if run_artifact is not None:
            run_artifact.finalize(
                status="completed",
                engine_name=output.engine_name,
                final_answer=response.answer,
            )
        self._log(
            "pipeline.completed",
            {
                "answer_type": type(response.answer).__name__,
                "engine_name": response.metadata.get("engine_name"),
            },
        )
        return response

    def _resolve_run_artifact(
        self,
        prepared: PreparedExecution,
    ) -> RunArtifactSession | None:
        if prepared.run_artifact is not None:
            return prepared.run_artifact
        if prepared.run_artifact_id is None:
            return None
        open_run = getattr(self.artifact_store, "open_run", None)
        if not callable(open_run):
            raise RuntimeError(
                "Prepared execution references an artifact, but the configured "
                "artifact store cannot reopen it."
            )
        prepared.run_artifact = open_run(prepared.run_artifact_id)
        return prepared.run_artifact

    def _confirm_spec(
        self,
        prepared: PreparedExecution,
    ) -> ExecutionSpec:
        spec = prepared.spec
        for _ in range(self.max_spec_revision_rounds + 1):
            confirmation_result = self.spec_confirmation.confirm(
                spec,
                prepared.session_context,
                prepared.user_context,
            )
            if isinstance(confirmation_result, SpecConfirmationDecision):
                if confirmation_result.action != "revise":
                    raise ValueError(
                        f"Unsupported spec confirmation action: {confirmation_result.action}"
                    )
                if not confirmation_result.feedback:
                    raise ValueError("Spec revision requires feedback.")
                spec = self.revise_spec(
                    prepared,
                    spec,
                    confirmation_result.feedback,
                )
                continue
            return confirmation_result

        raise RuntimeError("Maximum spec revision rounds exceeded.")


def _artifact_error(exc: BaseException) -> str:
    """Persist only the exception type so runtime secrets are never serialized."""

    return f"{type(exc).__name__}: runtime phase failed"


def _final_response_from_engine_output(
    output: EngineOutput,
    *,
    include_evidence: bool,
) -> FinalResponse:
    answer = output.answer if output.answer is not None else output.result
    return FinalResponse(
        answer=str(answer),
        evidence=output.evidence if include_evidence else None,
        metadata={
            **dict(output.metadata),
            "engine_name": output.engine_name,
        },
    )
