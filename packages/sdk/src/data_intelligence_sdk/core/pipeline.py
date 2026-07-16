"""High-level orchestration boundary for the architecture flow."""

from __future__ import annotations

from contextlib import nullcontext

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    FinalResponse,
    Intent,
    PreparedExecution,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.deep_agent_sandbox import SandboxSessionProvider
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.runtime.mcp_client import MCPMethodClient
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
        evidence_collector: object,
        synthesizer: object,
        method_hub: object | None = None,
        mcp_client: MCPMethodClient | None = None,
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
        self.evidence_collector = evidence_collector
        self.synthesizer = synthesizer
        self.method_hub = method_hub
        self.mcp_client = mcp_client
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
            confirmed_spec = self._confirm_spec(
                prepared.spec,
                prepared.query,
                prepared.intent,
                prepared.corpus_package,
                prepared.session_context,
                prepared.user_context,
            )
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
                        run_artifact.artifact_ref
                        if run_artifact is not None
                        else None
                    ),
                },
            )
            intent = self.intent_analyzer.analyze(
                query, corpus_package, session_context, user_context
            )
            self._log("pipeline.intent_analyzed", {"intent": intent})
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
            run_artifact=run_artifact,
            run_artifact_id=(
                run_artifact.run_id if run_artifact is not None else None
            ),
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
        revise = getattr(self.spec_builder, "revise", None)
        if revise is None:
            raise TypeError(
                "Spec confirmation requested revision, but spec_builder has no revise method."
            )
        self._log(
            "pipeline.spec_revision_started",
            {"intent": prepared.intent, "feedback": feedback},
        )
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
        return revised

    def execute_confirmed_spec(
        self,
        prepared: PreparedExecution,
        confirmed_spec: ExecutionSpec,
    ) -> FinalResponse:
        """Execute a previously prepared and explicitly confirmed spec."""

        if not confirmed_spec.confirmed:
            raise ValueError("Execution spec must be confirmed before engine selection.")
        run_artifact = self._resolve_run_artifact(prepared)
        self._log(
            "pipeline.spec_confirmed",
            {"confirmed": confirmed_spec.confirmed, "engine_hint": confirmed_spec.engine_hint},
        )
        phase = "engine_selection"
        engine = None
        try:
            engine = self.engine_registry.select(confirmed_spec)
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
                runtime = EngineRuntimeContext(
                    run_context=EngineRunContext(),
                    mcp_client=self.mcp_client,
                    method_hub=self.method_hub or MethodHub(),
                    interface_registry=self.interface_registry,
                    interface_builder=self.interface_builder,
                    sandbox_executor=self.sandbox_executor,
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
        try:
            if not self.include_evidence:
                response = FinalResponse(
                    answer=str(output.result),
                    evidence=None,
                    metadata={"engine_name": output.engine_name},
                )
            else:
                evidence = self.evidence_collector.collect(confirmed_spec, output)
                self._log(
                    "pipeline.evidence_collected",
                    {
                        "source_count": len(evidence.sources),
                        "step_count": len(evidence.steps),
                        "method_call_count": len(evidence.method_calls),
                    },
                )
                response = self.synthesizer.synthesize(
                    confirmed_spec,
                    output,
                    evidence,
                )
        except Exception as exc:
            if run_artifact is not None:
                run_artifact.finalize(
                    status="failed",
                    engine_name=output.engine_name,
                    failure_phase="response_synthesis",
                    error=_artifact_error(exc),
                )
            raise

        artifact_ref = (
            run_artifact.finalize(
                status="completed",
                engine_name=output.engine_name,
                final_answer=response.answer,
            )
            if run_artifact is not None
            else None
        )
        if artifact_ref is not None:
            response.metadata["artifact_ref"] = artifact_ref
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
        spec: ExecutionSpec,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None,
        user_context: UserContext | None,
    ) -> ExecutionSpec:
        for _ in range(self.max_spec_revision_rounds + 1):
            confirmation_result = self.spec_confirmation.confirm(
                spec, session_context, user_context
            )
            if isinstance(confirmation_result, SpecConfirmationDecision):
                if confirmation_result.action != "revise":
                    raise ValueError(
                        f"Unsupported spec confirmation action: {confirmation_result.action}"
                    )
                if not confirmation_result.feedback:
                    raise ValueError("Spec revision requires feedback.")
                prepared = PreparedExecution(
                    query=query,
                    intent=intent,
                    corpus_package=corpus_package,
                    spec=spec,
                    session_context=session_context,
                    user_context=user_context,
                )
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
