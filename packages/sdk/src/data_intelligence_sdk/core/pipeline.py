"""High-level orchestration boundary for the architecture flow."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    FinalResponse,
    Intent,
    IntentAnalysis,
    PreparedExecution,
    PreparedMarkdownExecution,
    PreprocessingStep,
    SessionContext,
    UserContext,
    UserQuery,
)
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
        evidence_collector: object | None = None,
        synthesizer: object | None = None,
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
        markdown_spec_builder: object | None = None,
        markdown_report_engine: object | None = None,
        default_organization_id: str = "test-org",
    ) -> None:
        self.intent_analyzer = intent_analyzer
        self.spec_builder = spec_builder
        self.spec_confirmation = spec_confirmation
        self.engine_registry = engine_registry
        self.evidence_collector = evidence_collector
        self.synthesizer = synthesizer
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
        self.markdown_spec_builder = markdown_spec_builder
        self.markdown_report_engine = markdown_report_engine
        self.default_organization_id = default_organization_id

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
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> FinalResponse:
        """Run the full data intelligence flow."""

        prepared = self.prepare_spec(
            query,
            session_context=session_context,
            user_context=user_context,
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
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> PreparedExecution:
        """Analyze intent and build a draft spec without selecting an engine."""

        run_artifact = self._create_run_artifact(query)
        try:
            self._log(
                "pipeline.start",
                {
                    "query": query.text,
                    "artifact_ref": (
                        run_artifact.artifact_ref if run_artifact is not None else None
                    ),
                },
            )
            analyzed_intent = self._analyze_intent(
                query,
                session_context,
                user_context,
            )
            intent_analysis = _normalize_intent_analysis(analyzed_intent)
            builder_intent_analysis = (
                analyzed_intent
                if hasattr(analyzed_intent, "intent")
                else intent_analysis
            )
            intent = intent_analysis.intent
            intent_payload = _intent_event_payload(analyzed_intent, intent_analysis)
            self._log("pipeline.intent_analyzed", intent_payload)
            self._record_artifact_event(
                run_artifact,
                phase="intent",
                event_type="intent.analyzed",
                payload=intent_payload,
            )
            spec = self._build_spec(
                query=query,
                intent=intent,
                intent_analysis=builder_intent_analysis,
                session_context=session_context,
                user_context=user_context,
            )
            spec.preprocessing_steps = list(intent_analysis.preprocessing_steps)
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
            spec=spec,
            session_context=session_context,
            user_context=user_context,
            intent_analysis=builder_intent_analysis,
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
                session_context=prepared.session_context,
                user_context=prepared.user_context,
            )
        else:
            revised = revise(
                previous_spec=previous_spec,
                user_feedback=feedback,
                query=prepared.query,
                intent=prepared.intent,
                session_context=prepared.session_context,
                user_context=prepared.user_context,
            )
        if prepared.intent_analysis is not None:
            revised.preprocessing_steps = list(
                _normalize_intent_analysis(
                    prepared.intent_analysis
                ).preprocessing_steps
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

    def prepare_markdown(
        self,
        query: UserQuery,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> PreparedMarkdownExecution:
        """Prepare a direct Markdown spec without a caller corpus package."""

        if self.markdown_spec_builder is None:
            raise RuntimeError("Markdown spec builder is not configured.")
        run_artifact = self._create_run_artifact(query)
        try:
            self._log(
                "pipeline.start",
                {
                    "query": query.text,
                    "artifact_ref": (
                        run_artifact.artifact_ref
                        if run_artifact is not None
                        else None
                    ),
                },
            )
            analyzed_intent = self._analyze_intent(
                query,
                session_context,
                user_context,
            )
            intent_analysis = _normalize_intent_analysis(analyzed_intent)
            intent_payload = _intent_event_payload(
                analyzed_intent,
                intent_analysis,
            )
            self._log("pipeline.intent_analyzed", intent_payload)
            self._record_artifact_event(
                run_artifact,
                phase="intent",
                event_type="intent.analyzed",
                payload=intent_payload,
            )
            markdown = self.markdown_spec_builder.build(query, intent_analysis)
            spec_payload = {
                "intent": intent_analysis.intent,
                "format": "markdown",
                "character_count": len(markdown),
                "spec_markdown": markdown,
            }
            self._log("pipeline.spec_built", spec_payload)
            self._record_artifact_event(
                run_artifact,
                phase="spec_builder",
                event_type="spec.built",
                payload=spec_payload,
            )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    failure_phase="spec_preparation",
                    error=_artifact_error(exc),
                )
            raise
        return PreparedMarkdownExecution(
            query=query,
            intent_analysis=intent_analysis,
            spec_markdown=markdown,
            session_context=session_context,
            user_context=user_context,
            run_artifact=run_artifact,
            run_artifact_id=(
                run_artifact.run_id if run_artifact is not None else None
            ),
        )

    def execute_confirmed_markdown(
        self,
        prepared: PreparedMarkdownExecution,
        spec_markdown: str,
    ) -> FinalResponse:
        """Execute Markdown through the configured Report Engine boundary."""

        if self.markdown_report_engine is None:
            raise RuntimeError("Markdown report engine is not configured.")
        run_artifact = self._resolve_run_artifact(prepared)
        self._record_artifact_event(
            run_artifact,
            phase="spec_builder",
            event_type="spec.confirmed",
            payload={
                "format": "markdown",
                "intent": prepared.intent_analysis.intent,
                "spec_markdown": spec_markdown,
            },
        )
        self._log(
            "pipeline.spec_confirmed",
            {"format": "markdown", "intent": prepared.intent_analysis.intent},
        )
        phase = "sandbox_provisioning"
        try:
            sandbox_context = (
                self.sandbox_provider.open()
                if self.sandbox_provider is not None
                else nullcontext(None)
            )
            with sandbox_context as sandbox:
                phase = "engine_execution"
                _stage_uploaded_files(sandbox, prepared.session_context)
                sandbox_executor = self.sandbox_executor
                if sandbox_executor is None and sandbox is not None:
                    sandbox_executor = RequestSandboxExecutor(sandbox, run_artifact)

                def record_runtime_event(**event: Any) -> dict[str, Any]:
                    return self._record_runtime_event(run_artifact, **event)

                runtime = EngineRuntimeContext(
                    run_context=EngineRunContext(event_recorder=record_runtime_event),
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
                result = self.markdown_report_engine.run_markdown(
                    spec_markdown=spec_markdown,
                    organization_id=self.default_organization_id,
                    runtime=runtime,
                    user_context=prepared.user_context,
                    user_query=prepared.query,
                )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    engine_name="report",
                    failure_phase=phase,
                    error=_artifact_error(exc),
                )
            raise

        response = (
            result
            if isinstance(result, FinalResponse)
            else FinalResponse(answer=str(result), metadata={"engine_name": "report"})
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
                engine_name=str(response.metadata.get("engine_name") or "report"),
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

    def execute_report_direct(
        self,
        query: UserQuery,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> FinalResponse:
        """Execute the Report engine from the raw query without a spec lifecycle."""

        if self.markdown_report_engine is None:
            raise RuntimeError("Markdown report engine is not configured.")
        run_artifact = self._create_run_artifact(query)
        self._log(
            "pipeline.start",
            {
                "query": query.text,
                "artifact_ref": (
                    run_artifact.artifact_ref if run_artifact is not None else None
                ),
            },
        )
        self._record_artifact_event(
            run_artifact,
            phase="engine_execution",
            event_type="report.direct_started",
            payload={"query": query.text},
        )
        phase = "sandbox_provisioning"
        try:
            sandbox_context = (
                self.sandbox_provider.open()
                if self.sandbox_provider is not None
                else nullcontext(None)
            )
            with sandbox_context as sandbox:
                phase = "engine_execution"
                _stage_uploaded_files(sandbox, session_context)
                sandbox_executor = self.sandbox_executor
                if sandbox_executor is None and sandbox is not None:
                    sandbox_executor = RequestSandboxExecutor(sandbox, run_artifact)

                def record_runtime_event(**event: Any) -> dict[str, Any]:
                    return self._record_runtime_event(run_artifact, **event)

                runtime = EngineRuntimeContext(
                    run_context=EngineRunContext(event_recorder=record_runtime_event),
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
                result = self.markdown_report_engine.run_markdown(
                    spec_markdown=query.text,
                    organization_id=self.default_organization_id,
                    runtime=runtime,
                    user_context=user_context,
                    user_query=query,
                )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    engine_name="report",
                    failure_phase=phase,
                    error=_artifact_error(exc),
                )
            raise

        response = (
            result
            if isinstance(result, FinalResponse)
            else FinalResponse(answer=str(result), metadata={"engine_name": "report"})
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
                engine_name=str(response.metadata.get("engine_name") or "report"),
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
                self.sandbox_provider.open()
                if self.sandbox_provider is not None
                else nullcontext(None)
            )
            with sandbox_context as sandbox:
                phase = "engine_execution"
                _stage_uploaded_files(sandbox, prepared.session_context)
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
                    EngineInput(
                        query=prepared.query,
                        spec=confirmed_spec,
                        runtime=runtime,
                        user_context=prepared.user_context,
                    )
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
        prepared: PreparedExecution | PreparedMarkdownExecution,
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

    def _create_run_artifact(self, query: UserQuery) -> RunArtifactSession | None:
        create_run = getattr(self.artifact_store, "create_run", None)
        if not callable(create_run):
            return None
        return create_run(query)

    def _build_spec(
        self,
        *,
        query: UserQuery,
        intent: Intent,
        intent_analysis: object,
        session_context: SessionContext | None,
        user_context: UserContext | None,
    ) -> ExecutionSpec:
        build_with_intent_analysis = getattr(
            self.spec_builder,
            "build_with_intent_analysis",
            None,
        )
        if callable(build_with_intent_analysis):
            return build_with_intent_analysis(
                query=query,
                intent_analysis=intent_analysis,
                session_context=session_context,
                user_context=user_context,
            )

        return self.spec_builder.build(
            query=query,
            intent=intent,
            session_context=session_context,
            user_context=user_context,
        )

    def _analyze_intent(
        self,
        query: UserQuery,
        session_context: SessionContext | None,
        user_context: UserContext | None,
    ) -> object:
        analyze_details = getattr(self.intent_analyzer, "analyze_details", None)
        if callable(analyze_details):
            return analyze_details(query, session_context, user_context)
        return self.intent_analyzer.analyze(query, session_context, user_context)

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

def _normalize_intent_analysis(value: object) -> IntentAnalysis:
    if isinstance(value, IntentAnalysis):
        return value
    intent = getattr(value, "intent", value)
    if intent not in {"reason", "report", "general", "unknown"}:
        raise TypeError("Intent analyzer must return an Intent or IntentAnalysis.")
    catalog_intent_id = getattr(value, "catalog_intent_id", None) or getattr(
        value,
        "catalog_intent",
        None,
    )
    raw_steps = getattr(value, "preprocessing_steps", None)
    if raw_steps is None:
        raw_steps = getattr(value, "processing_steps", None)
    return IntentAnalysis(
        intent=intent,
        catalog_intent_id=(str(catalog_intent_id) if catalog_intent_id else None),
        preprocessing_steps=_normalize_preprocessing_steps(raw_steps),
        metadata=_intent_metadata(value),
    )

def _normalize_preprocessing_steps(value: object) -> list[PreprocessingStep]:
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = ((getattr(item, "name", str(index)), item) for index, item in enumerate(value))
    else:
        return []
    steps: list[PreprocessingStep] = []
    for name, raw_step in items:
        if isinstance(raw_step, PreprocessingStep):
            steps.append(raw_step)
            continue
        if not isinstance(raw_step, dict):
            continue
        steps.append(
            PreprocessingStep(
                name=str(name),
                order=int(raw_step.get("order", 0)),
                step_type=str(raw_step.get("step_type") or raw_step.get("type") or "prepare"),
                description=_optional_string(raw_step.get("description")),
                capability=_optional_string(raw_step.get("capability")),
                required=bool(raw_step.get("required", False)),
                depends_on=[str(item) for item in raw_step.get("depends_on", [])]
                if isinstance(raw_step.get("depends_on"), list)
                else [],
            )
        )
    return sorted(steps, key=lambda step: (step.order, step.name))

def _intent_event_payload(original: object, analysis: IntentAnalysis) -> dict[str, object]:
    event_payload = getattr(original, "event_payload", None)
    if callable(event_payload):
        payload = dict(event_payload())
        payload.setdefault("intent", analysis.intent)
        payload.setdefault("catalog_intent_id", analysis.catalog_intent_id)
        return payload
    return {
        "intent": analysis.intent,
        "catalog_intent_id": analysis.catalog_intent_id,
        "preprocessing_step_count": len(analysis.preprocessing_steps),
    }

def _intent_metadata(value: object) -> dict[str, Any]:
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    payload = {
        "source": getattr(value, "source", None),
        "confidence": getattr(value, "confidence", None),
        "score": getattr(value, "score", None),
    }
    return {key: item for key, item in payload.items() if item is not None}

def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

def _stage_uploaded_files(
    sandbox: object | None,
    session_context: SessionContext | None,
) -> None:
    if sandbox is None or session_context is None:
        return
    records = session_context.state.get("_uploaded_files_to_stage", [])
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict):
            continue
        filename = Path(str(record.get("filename") or "")).name
        url = str(record.get("url") or "")
        if not filename or not url.startswith(("http://", "https://")):
            continue
        try:
            response = httpx.get(url, timeout=60.0)
            response.raise_for_status()
            content = response.content
        except httpx.HTTPError:
            continue
        write = getattr(sandbox, "write", None)
        if callable(write):
            write(filename, content)
        source_paths = getattr(sandbox, "source_paths", None)
        if isinstance(source_paths, dict):
            source_paths[filename] = filename

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
