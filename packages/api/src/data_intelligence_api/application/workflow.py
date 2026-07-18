"""Map validated API requests into the example SDK workflow."""

from __future__ import annotations

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
    PreparedExecution,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_api.infrastructure.workflow.pipeline_factory import create_example_pipeline

from data_intelligence_api.domain.workflow import WorkflowInvocation
from data_intelligence_api.http.schemas.responses import CreateResponseRequest


DEFAULT_QUERY = "Analyze this data corpus."
PipelineFactory = Callable[..., DataIntelligencePipeline]


class SourceValidationError(ValueError):
    """Raised when a requested corpus source is not allowed."""


def resolve_sources(sources: list[str], data_corpus_root: Path) -> list[str]:
    if not sources:
        raise SourceValidationError("At least one data corpus source is required.")
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
) -> WorkflowInvocation:
    query_text = (request.input or "").strip() or DEFAULT_QUERY
    return WorkflowInvocation(
        query=UserQuery(
            text=query_text,
            user_id=request.user_id,
            session_id=request.session_id,
        ),
        corpus_package=DataCorpusPackage(
            sources=resolve_sources(
                request.data_corpus_package.sources,
                data_corpus_root,
            ),
            schemas=request.data_corpus_package.schemas,
            metadata=request.data_corpus_package.metadata,
        ),
        session_context=SessionContext(session_id=request.session_id),
        user_context=UserContext(user_id=request.user_id),
    )


def default_pipeline_factory(*, logger: RuntimeLogger) -> DataIntelligencePipeline:
    return create_example_pipeline(
        logger=logger,
        config_path=os.getenv("MODEL_CONFIG_PATH") or None,
        use_llm_spec_builder=True,
        intent_service_base_url=os.getenv("INTENT_SERVICE_BASE_URL") or None,
    )


def execute_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FinalResponse:
    pipeline = pipeline_factory(logger=logger)
    return pipeline.run(
        invocation.query,
        invocation.corpus_package,
        invocation.session_context,
        invocation.user_context,
    )


def prepare_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> PreparedExecution:
    pipeline = pipeline_factory(logger=logger)
    return pipeline.prepare_spec(
        invocation.query,
        invocation.corpus_package,
        invocation.session_context,
        invocation.user_context,
    )


def revise_workflow(
    prepared: PreparedExecution,
    previous_spec: ExecutionSpec,
    feedback: str,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> ExecutionSpec:
    pipeline = pipeline_factory(logger=logger)
    return pipeline.revise_spec(prepared, previous_spec, feedback)


def execute_prepared_workflow(
    prepared: PreparedExecution,
    confirmed_spec: ExecutionSpec,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
) -> FinalResponse:
    pipeline = pipeline_factory(logger=logger)
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
    )


def prepared_to_payload(prepared: PreparedExecution) -> dict:
    return {
        "version": 1,
        "query": asdict(prepared.query),
        "intent": prepared.intent,
        "corpus_package": asdict(prepared.corpus_package),
        "session_context": asdict(prepared.session_context)
        if prepared.session_context is not None
        else None,
        "user_context": asdict(prepared.user_context)
        if prepared.user_context is not None
        else None,
        "run_artifact_id": prepared.run_artifact_id,
    }


def prepared_from_payload(payload: dict, spec: ExecutionSpec) -> PreparedExecution:
    session_payload = payload.get("session_context")
    user_payload = payload.get("user_context")
    return PreparedExecution(
        query=UserQuery(**payload["query"]),
        intent=payload["intent"],
        corpus_package=DataCorpusPackage(**payload["corpus_package"]),
        spec=spec,
        session_context=SessionContext(**session_payload)
        if session_payload is not None
        else None,
        user_context=UserContext(**user_payload) if user_payload is not None else None,
        run_artifact_id=payload.get("run_artifact_id"),
    )
