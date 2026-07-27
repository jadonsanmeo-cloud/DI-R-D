"""Map validated API requests into the example SDK workflow."""

from __future__ import annotations

import inspect
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    CapabilityRequirement,
    ExecutionSpec,
    FinalResponse,
    IntentAnalysis,
    PreparedExecution,
    PreparedMarkdownExecution,
    PreprocessingStep,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.registry.engine_selector import EngineDescriptor
from data_intelligence_sdk.spec.markdown_builder import validate_spec_markdown
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)

from data_intelligence_api.application.runtime_capabilities import (
    resolve_method_hub,
    resolve_runtime_options,
)
from data_intelligence_api.domain.workflow import (
    WorkflowInvocation,
    WorkflowRuntimeOptions,
)
from data_intelligence_api.http.schemas.responses import CreateResponseRequest

DEFAULT_QUERY = "Analyze this data corpus."
PipelineFactory = Callable[..., DataIntelligencePipeline]

ENGINE_ROUTE_MAP = {
    "general": "general_purpose",
    "reason": "reason",
    "report": "report",
}

class RequestedEngineSelector:
    def __init__(self, engine_name: str) -> None:
        self.engine_name = engine_name

    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        del spec
        names = {engine.name for engine in engines}
        if self.engine_name not in names:
            raise ValueError(f"Requested engine is not registered: {self.engine_name}")
        return self.engine_name


class SourceValidationError(ValueError):
    """Raised when a requested corpus source is not allowed."""


def resolve_sources(sources: list[str], data_corpus_root: Path) -> list[str]:
    root = data_corpus_root.resolve()
    resolved_sources: list[str] = []
    for source in sources:
        source_path = Path(source)
        if not source_path.is_absolute() and urlparse(source).scheme:
            raise SourceValidationError(
                f"Remote data source references are not supported: {source}"
            )
        candidate = (
            source_path.resolve()
            if source_path.is_absolute()
            else (root / source_path).resolve()
        )
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise SourceValidationError(
                f"Data source is outside DATA_CORPUS_ROOT: {source}"
            ) from error
        if not candidate.exists():
            raise SourceValidationError(f"Data source does not exist: {source}")
        resolved_sources.append(str(candidate))
    return resolved_sources


def build_workflow_invocation(
    request: CreateResponseRequest,
    data_corpus_root: Path,
    *,
    method_hub_default_enabled: bool = False,
) -> WorkflowInvocation:
    query_text = (request.input or "").strip() or DEFAULT_QUERY
    package = request.data_corpus_package
    return WorkflowInvocation(
        query=UserQuery(
            text=query_text,
            user_id=request.user_id,
            session_id=request.session_id,
        ),
        corpus_package=DataCorpusPackage(
            sources=resolve_sources(
                package.sources if package is not None else [],
                data_corpus_root,
            ),
            schemas=package.schemas if package is not None else {},
            metadata=package.metadata if package is not None else {},
        ),
        session_context=SessionContext(session_id=request.session_id),
        user_context=UserContext(user_id=request.user_id),
        runtime_options=resolve_runtime_options(
            request.runtime_options,
            default_enabled=method_hub_default_enabled,
        ),
    )


def default_pipeline_factory(
    *,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions | None = None,
) -> DataIntelligencePipeline:
    config_manager = ConfigManager(os.getenv("MODEL_CONFIG_PATH") or None)
    method_hub_settings = config_manager.method_hub_settings()
    intent_service_settings = config_manager.intent_service_settings()
    resolved_options = runtime_options or WorkflowRuntimeOptions(
        method_hub_enabled=False
    )
    requested_engine = ENGINE_ROUTE_MAP.get(resolved_options.engine or "")
    resolved_method_hub = resolve_method_hub(
        resolved_options,
        endpoint=method_hub_settings.endpoint,
    )
    method_hub_kwargs = (
        {
            "mcp_tools": resolved_method_hub.tools,
            "method_hub_enabled": True,
        }
        if resolved_options.method_hub_enabled
        else {}
    )
    engine_kwargs = (
        {"engine_selector": RequestedEngineSelector(requested_engine)}
        if requested_engine is not None
        else {}
    )
    return create_example_pipeline(
        logger=logger,
        config_manager=config_manager,
        use_llm_spec_builder=True,
        intent_service_base_url=(
            os.getenv("INTENT_SERVICE_BASE_URL")
            or (intent_service_settings.endpoint if intent_service_settings.enabled else None)
        ),
        default_organization_id=os.getenv("DEFAULT_ORGANIZATION_ID", "test-org"),
        mcp_client=resolved_method_hub.client,
        **engine_kwargs,
        **method_hub_kwargs,
    )


def _create_pipeline(
    pipeline_factory: PipelineFactory,
    *,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
) -> DataIntelligencePipeline:
    try:
        parameters = inspect.signature(pipeline_factory).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    supports_runtime_options = any(
        parameter.name == "runtime_options"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if supports_runtime_options:
        return pipeline_factory(logger=logger, runtime_options=runtime_options)
    return pipeline_factory(logger=logger)


def execute_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FinalResponse:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=invocation.runtime_options,
    )
    return pipeline.run(
        invocation.query,
        session_context=invocation.session_context,
        user_context=invocation.user_context,
    )


def prepare_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> PreparedMarkdownExecution:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=invocation.runtime_options,
    )
    return pipeline.prepare_markdown(
        invocation.query,
        invocation.session_context,
        invocation.user_context,
    )

def revise_markdown_workflow(
    prepared: PreparedMarkdownExecution,
    spec_markdown: str,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> str:
    del prepared, runtime_options, pipeline_factory
    logger.log("pipeline.spec_revision_started", {"format": "markdown"})
    revised = validate_spec_markdown(spec_markdown)
    logger.log("pipeline.spec_revised", {"format": "markdown"})
    return revised

def execute_prepared_markdown_workflow(
    prepared: PreparedMarkdownExecution,
    spec_markdown: str,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FinalResponse:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=runtime_options,
    )
    if runtime_options.engine in {"general", "reason"}:
        spec = _execution_spec_from_markdown(prepared, spec_markdown, runtime_options)
        prepared_execution = PreparedExecution(
            query=prepared.query,
            intent=spec.intent,
            corpus_package=DataCorpusPackage(),
            spec=spec,
            session_context=prepared.session_context,
            user_context=prepared.user_context,
            intent_analysis=prepared.intent_analysis,
            run_artifact=prepared.run_artifact,
            run_artifact_id=prepared.run_artifact_id,
        )
        return pipeline.execute_confirmed_spec(prepared_execution, spec)
    return pipeline.execute_confirmed_markdown(prepared, spec_markdown)

def _execution_spec_from_markdown(
    prepared: PreparedMarkdownExecution,
    spec_markdown: str,
    runtime_options: WorkflowRuntimeOptions,
) -> ExecutionSpec:
    intent = runtime_options.engine or prepared.intent_analysis.intent
    if intent not in {"general", "reason", "report"}:
        intent = prepared.intent_analysis.intent
    return ExecutionSpec(
        intent=intent,
        objective=validate_spec_markdown(spec_markdown),
        confirmed=True,
        engine_hint=ENGINE_ROUTE_MAP.get(runtime_options.engine or ""),
        preprocessing_steps=list(prepared.intent_analysis.preprocessing_steps),
    )


def revise_workflow(
    prepared: PreparedExecution,
    previous_spec: ExecutionSpec,
    feedback: str,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> ExecutionSpec:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=runtime_options,
    )
    return pipeline.revise_spec(prepared, previous_spec, feedback)


def execute_prepared_workflow(
    prepared: PreparedExecution,
    confirmed_spec: ExecutionSpec,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FinalResponse:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=runtime_options,
    )
    return pipeline.execute_confirmed_spec(prepared, confirmed_spec)


def spec_to_payload(spec: ExecutionSpec) -> dict:
    return asdict(spec)


def spec_from_payload(payload: dict) -> ExecutionSpec:
    return ExecutionSpec(
        intent=payload["intent"],
        objective=payload["objective"],
        data_requirements=list(payload.get("data_requirements", [])),
        capability_requirements=[
            CapabilityRequirement(**item)
            for item in payload.get("capability_requirements", [])
        ],
        constraints=dict(payload.get("constraints", {})),
        confirmed=bool(payload.get("confirmed", False)),
        engine_hint=payload.get("engine_hint"),
        preprocessing_steps=[
            PreprocessingStep(**item)
            for item in payload.get("preprocessing_steps", [])
        ],
    )

def markdown_spec_to_payload(markdown: str) -> dict[str, str]:
    return {"spec_markdown": validate_spec_markdown(markdown)}

def markdown_spec_from_payload(payload: dict) -> str:
    if set(payload) != {"spec_markdown"}:
        raise ValueError("Legacy structured spec payloads are unsupported.")
    return validate_spec_markdown(payload["spec_markdown"])


def prepared_to_payload(prepared: PreparedExecution) -> dict:
    return {
        "version": 1,
        "query": asdict(prepared.query),
        "intent": prepared.intent,
        "corpus_package": asdict(prepared.corpus_package),
        "session_context": (
            asdict(prepared.session_context)
            if prepared.session_context is not None
            else None
        ),
        "user_context": (
            asdict(prepared.user_context) if prepared.user_context is not None else None
        ),
        "run_artifact_id": prepared.run_artifact_id,
        "intent_analysis": (
            asdict(prepared.intent_analysis)
            if prepared.intent_analysis is not None
            else None
        ),
    }


def prepared_from_payload(payload: dict, spec: ExecutionSpec) -> PreparedExecution:
    session_payload = payload.get("session_context")
    user_payload = payload.get("user_context")
    analysis_payload = payload.get("intent_analysis")
    return PreparedExecution(
        query=UserQuery(**payload["query"]),
        intent=payload["intent"],
        corpus_package=DataCorpusPackage(**payload["corpus_package"]),
        spec=spec,
        session_context=(
            SessionContext(**session_payload) if session_payload is not None else None
        ),
        user_context=UserContext(**user_payload) if user_payload is not None else None,
        run_artifact_id=payload.get("run_artifact_id"),
        intent_analysis=(
            IntentAnalysis(
                intent=analysis_payload["intent"],
                catalog_intent_id=analysis_payload.get("catalog_intent_id"),
                preprocessing_steps=[
                    PreprocessingStep(**item)
                    for item in analysis_payload.get("preprocessing_steps", [])
                ],
                metadata=dict(analysis_payload.get("metadata", {})),
            )
            if isinstance(analysis_payload, dict)
            else None
        ),
    )

def prepared_markdown_to_payload(prepared: PreparedMarkdownExecution) -> dict:
    return {
        "version": 2,
        "query": asdict(prepared.query),
        "intent_analysis": asdict(prepared.intent_analysis),
        "session_context": (
            asdict(prepared.session_context)
            if prepared.session_context is not None
            else None
        ),
        "user_context": (
            asdict(prepared.user_context) if prepared.user_context is not None else None
        ),
        "run_artifact_id": prepared.run_artifact_id,
    }

def prepared_markdown_from_payload(
    payload: dict,
    spec_markdown: str,
) -> PreparedMarkdownExecution:
    analysis_payload = payload.get("intent_analysis")
    if not isinstance(analysis_payload, dict):
        raise ValueError("Legacy prepared execution payloads are unsupported.")
    session_payload = payload.get("session_context")
    user_payload = payload.get("user_context")
    return PreparedMarkdownExecution(
        query=UserQuery(**payload["query"]),
        intent_analysis=IntentAnalysis(
            intent=analysis_payload["intent"],
            catalog_intent_id=analysis_payload.get("catalog_intent_id"),
            preprocessing_steps=[
                PreprocessingStep(**item)
                for item in analysis_payload.get("preprocessing_steps", [])
            ],
            metadata=dict(analysis_payload.get("metadata", {})),
        ),
        spec_markdown=validate_spec_markdown(spec_markdown),
        session_context=(
            SessionContext(**session_payload) if session_payload is not None else None
        ),
        user_context=UserContext(**user_payload) if user_payload is not None else None,
        run_artifact_id=payload.get("run_artifact_id"),
    )
