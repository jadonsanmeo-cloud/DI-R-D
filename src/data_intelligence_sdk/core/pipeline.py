"""High-level orchestration boundary for the architecture flow."""

from __future__ import annotations

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    FinalResponse,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.runtime.run_context import EngineRunContext
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
        interface_registry: object | None = None,
        interface_builder: object | None = None,
        sandbox_executor: object | None = None,
        artifact_store: object | None = None,
        log_store: object | None = None,
        resource_manager: object | None = None,
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
        self.interface_registry = interface_registry
        self.interface_builder = interface_builder
        self.sandbox_executor = sandbox_executor
        self.artifact_store = artifact_store
        self.log_store = log_store
        self.resource_manager = resource_manager
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

        self._log(
            "pipeline.start",
            {
                "query": query.text,
                "source_count": len(corpus_package.sources),
                "has_schema": bool(corpus_package.schemas),
                "has_metadata": bool(corpus_package.metadata),
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
        confirmed_spec = self._confirm_spec(
            spec,
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        self._log(
            "pipeline.spec_confirmed",
            {"confirmed": confirmed_spec.confirmed, "engine_hint": confirmed_spec.engine_hint},
        )
        engine = self.engine_registry.select(confirmed_spec)
        self._log(
            "pipeline.engine_selected",
            {"engine_name": getattr(engine, "name", type(engine).__name__)},
        )
        runtime = EngineRuntimeContext(
            run_context=EngineRunContext(),
            method_hub=self.method_hub or MethodHub(),
            interface_registry=self.interface_registry,
            interface_builder=self.interface_builder,
            sandbox_executor=self.sandbox_executor,
            artifact_store=self.artifact_store,
            log_store=self.log_store,
            resource_manager=self.resource_manager,
        )
        output = engine.run(confirmed_spec, corpus_package, runtime, user_context)
        self._log(
            "pipeline.engine_completed",
            {
                "engine_name": output.engine_name,
                "step_count": len(output.trace.steps),
                "method_call_count": len(output.trace.method_calls),
            },
        )
        evidence = self.evidence_collector.collect(confirmed_spec, output)
        self._log(
            "pipeline.evidence_collected",
            {
                "source_count": len(evidence.sources),
                "step_count": len(evidence.steps),
                "method_call_count": len(evidence.method_calls),
            },
        )
        response = self.synthesizer.synthesize(confirmed_spec, output, evidence)
        self._log(
            "pipeline.completed",
            {
                "answer_type": type(response.answer).__name__,
                "engine_name": response.metadata.get("engine_name"),
            },
        )
        return response
        return self.synthesizer.synthesize(confirmed_spec, output, evidence)

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
                revise = getattr(self.spec_builder, "revise", None)
                if revise is None:
                    raise TypeError(
                        "Spec confirmation requested revision, but spec_builder has no revise method."
                    )
                spec = revise(
                    previous_spec=spec,
                    user_feedback=confirmation_result.feedback,
                    query=query,
                    intent=intent,
                    corpus_package=corpus_package,
                    session_context=session_context,
                    user_context=user_context,
                )
                continue
            return confirmation_result

        raise RuntimeError("Maximum spec revision rounds exceeded.")
