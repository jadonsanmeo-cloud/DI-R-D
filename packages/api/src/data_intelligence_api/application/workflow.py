"""Map validated API requests into the example SDK workflow."""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    ExecutionSpec,
    FinalResponse,
    Intent,
    IntentAnalysis,
    PreparedExecution,
    PreparedMarkdownExecution,
    PreprocessingStep,
    SessionContext,
    UploadedFile,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext
from data_intelligence_sdk.registry.engine_registry import SelectedEngine
from data_intelligence_sdk.runtime.config import ConfigManager
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.spec.markdown_builder import validate_spec_markdown

from data_intelligence_api.application.runtime_capabilities import (
    resolve_method_hub,
    resolve_runtime_options,
)
from data_intelligence_api.domain.workflow import (
    WorkflowInvocation,
    WorkflowRuntimeOptions,
    WorkflowName,
)
from data_intelligence_api.http.schemas.runtime_inputs import WorkflowRequest
from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportEngine,
)
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)

DEFAULT_QUERY = "Analyze this data corpus."
PipelineFactory = Callable[..., DataIntelligencePipeline]

ENGINE_ROUTE_MAP = {
    "general": "general",
    "reason": "reason",
    "report": "report",
}


class SourceValidationError(ValueError):
    """Raised when a requested corpus source is not allowed."""


def _uploaded_file_records(
    request: WorkflowRequest,
    data_corpus_root: Path,
) -> tuple[list[UploadedFile], list[dict[str, Any]]]:
    del data_corpus_root
    uploaded_files: list[UploadedFile] = []
    staging_records: list[dict[str, Any]] = []
    for item in request.uploaded_files:
        filename = Path(item.filename.strip() or "upload").name
        file_ref = item.metadata.get("file_ref")
        if not isinstance(file_ref, dict):
            raise SourceValidationError(
                f"Uploaded file is missing file_ref metadata: {filename}"
            )
        url = file_ref.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise SourceValidationError(
                f"Uploaded file is missing a readable file_ref.url: {filename}"
            )
        uploaded_files.append(UploadedFile(filename=filename))
        staging_records.append({"filename": filename, "url": url})
    return uploaded_files, staging_records


def build_workflow_invocation(
    request: WorkflowRequest,
    data_corpus_root: Path,
    *,
    method_hub_default_enabled: bool = False,
    memory_context: MemoryContext | None = None,
) -> WorkflowInvocation:
    query_text = (request.input or "").strip() or DEFAULT_QUERY
    uploaded_files, staging_records = _uploaded_file_records(
        request,
        data_corpus_root,
    )
    public_uploaded_files = [asdict(uploaded) for uploaded in uploaded_files]
    request_scope = {
        "organization_id": request.organization_id,
        "workspace_id": request.workspace_id,
        "workspace_ids": list(request.workspace_ids or []),
    }
    return WorkflowInvocation(
        query=UserQuery(
            text=query_text,
            user_id=request.user_id,
            session_id=request.session_id,
            metadata={
                "uploaded_files": public_uploaded_files,
                "history": [item.model_dump(mode="json") for item in request.history],
                **request_scope,
            },
        ),
        uploaded_files=uploaded_files,
        session_context=SessionContext(
            session_id=request.session_id,
            state={
                "uploaded_files": public_uploaded_files,
                "_uploaded_files_to_stage": staging_records,
                **request_scope,
            },
        ),
        user_context=UserContext(user_id=request.user_id),
        runtime_options=resolve_runtime_options(
            request.runtime_options,
            default_enabled=method_hub_default_enabled,
        ),
        memory_context=memory_context or MemoryContext(),
    )


def default_pipeline_factory(
    *,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions | None = None,
    execution_context: dict[str, Any] | None = None,
    execution_files: list[dict[str, Any]] | None = None,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    primary_source_id: str | None = None,
    discover_workspace_files: bool = False,
    workflow: WorkflowName = "report",
    operation_id: str | None = None,
    response_id: str | None = None,
    trace_id: str | None = None,
    model: str | None = None,
    language: str = "auto",
    history: list[dict[str, Any]] | None = None,
    gen_report_base_url: str | None = None,
    gen_report_public_url: str | None = None,
    include_method_hub: bool = True,
) -> DataIntelligencePipeline:
    config_manager = ConfigManager(os.getenv("MODEL_CONFIG_PATH") or None)
    method_hub_settings = config_manager.method_hub_settings()
    intent_service_settings = config_manager.intent_service_settings()
    resolved_options = runtime_options or WorkflowRuntimeOptions(
        method_hub_enabled=False
    )
    if workflow == "report" and resolved_options.workflow != "report":
        workflow = resolved_options.workflow
    resolved_method_hub = (
        resolve_method_hub(
            resolved_options,
            endpoint=method_hub_settings.endpoint,
        )
        if include_method_hub
        else None
    )
    report_base_url = gen_report_base_url or os.getenv("GEN_REPORT_API_URL")
    if report_base_url is None:
        report_base_url = "http://host.docker.internal:8011"
    markdown_report_engine = GenReportEngine(
        report_base_url,
        operation_id=operation_id or "",
        response_id=response_id or "",
        trace_id=trace_id,
        model=model,
        language=language,
        organization_id=organization_id or "test-org",
        history=history,
        public_base_url=gen_report_public_url or os.getenv("GEN_REPORT_PUBLIC_URL"),
        execution_context=execution_context,
        execution_files=execution_files,
        workspace_id=workspace_id,
        primary_source_id=primary_source_id,
        discover_workspace_files=discover_workspace_files,
        workflow=workflow,
    )
    return create_example_pipeline(
        logger=logger,
        config_manager=config_manager,
        execution_context=execution_context,
        use_llm_spec_builder=True,
        intent_service_base_url=(
            os.getenv("INTENT_SERVICE_BASE_URL")
            or (
                intent_service_settings.endpoint
                if intent_service_settings.enabled
                else None
            )
        ),
        default_organization_id=(
            organization_id or os.getenv("DEFAULT_ORGANIZATION_ID", "test-org")
        ),
        configure_default_sandbox=include_method_hub,
        markdown_report_engine=markdown_report_engine,
        mcp_client=(
            resolved_method_hub.client if resolved_method_hub is not None else None
        ),
        mcp_tools=(
            resolved_method_hub.tools
            if resolved_method_hub is not None and resolved_options.method_hub_enabled
            else ()
        ),
        method_hub_enabled=(
            resolved_options.method_hub_enabled
            if resolved_method_hub is not None
            else False
        ),
    )


def _create_pipeline(
    pipeline_factory: PipelineFactory,
    *,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    execution_context: dict[str, Any] | None = None,
    execution_files: list[dict[str, Any]] | None = None,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    primary_source_id: str | None = None,
    discover_workspace_files: bool = False,
    operation_id: str | None = None,
    response_id: str | None = None,
    trace_id: str | None = None,
    model: str | None = None,
    language: str = "auto",
    history: list[dict[str, Any]] | None = None,
    gen_report_base_url: str | None = None,
    gen_report_public_url: str | None = None,
    include_method_hub: bool = True,
) -> DataIntelligencePipeline:
    try:
        parameters = tuple(inspect.signature(pipeline_factory).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
    parameter_names = {parameter.name for parameter in parameters}
    supports_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    kwargs: dict[str, Any] = {"logger": logger}
    if supports_kwargs or "runtime_options" in parameter_names:
        kwargs["runtime_options"] = runtime_options
    if supports_kwargs or "execution_context" in parameter_names:
        kwargs["execution_context"] = execution_context
    if supports_kwargs or "execution_files" in parameter_names:
        kwargs["execution_files"] = execution_files
    if supports_kwargs or "organization_id" in parameter_names:
        kwargs["organization_id"] = organization_id
    if supports_kwargs or "workspace_id" in parameter_names:
        kwargs["workspace_id"] = workspace_id
    if supports_kwargs or "primary_source_id" in parameter_names:
        kwargs["primary_source_id"] = primary_source_id
    if supports_kwargs or "discover_workspace_files" in parameter_names:
        kwargs["discover_workspace_files"] = discover_workspace_files
    if supports_kwargs or "workflow" in parameter_names:
        kwargs["workflow"] = runtime_options.workflow
    if supports_kwargs or "include_method_hub" in parameter_names:
        kwargs["include_method_hub"] = include_method_hub
    optional_values = {
        "operation_id": operation_id,
        "response_id": response_id,
        "trace_id": trace_id,
        "model": model,
        "language": language,
        "history": history,
        "gen_report_base_url": gen_report_base_url,
        "gen_report_public_url": gen_report_public_url,
    }
    for name, value in optional_values.items():
        if supports_kwargs or name in parameter_names:
            kwargs[name] = value
    return pipeline_factory(**kwargs)


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
        memory_context=invocation.memory_context,
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
    execution_context: dict[str, Any] | None = None,
    execution_files: list[dict[str, Any]] | None = None,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    primary_source_id: str | None = None,
    discover_workspace_files: bool = False,
    operation_id: str | None = None,
    response_id: str | None = None,
    trace_id: str | None = None,
    model: str | None = None,
    language: str = "auto",
    history: list[dict[str, Any]] | None = None,
    gen_report_base_url: str | None = None,
    gen_report_public_url: str | None = None,
    memory_context: MemoryContext | None = None,
    selection: SelectedEngine | None = None,
) -> FinalResponse:
    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=runtime_options,
        execution_context=execution_context,
        execution_files=execution_files,
        organization_id=organization_id,
        workspace_id=workspace_id,
        primary_source_id=primary_source_id,
        discover_workspace_files=discover_workspace_files,
        operation_id=operation_id,
        response_id=response_id,
        trace_id=trace_id,
        model=model,
        language=language,
        history=history,
        gen_report_base_url=gen_report_base_url,
        gen_report_public_url=gen_report_public_url,
    )
    spec = _execution_spec_from_markdown(prepared, spec_markdown, runtime_options)
    prepared_execution = _confirmed_prepared_execution(prepared, spec)
    execution_kwargs: dict[str, Any] = {"memory_context": memory_context}
    if selection is not None:
        execution_kwargs["selection"] = selection
    return pipeline.execute_confirmed_spec(prepared_execution, spec, **execution_kwargs)


def select_prepared_markdown_engine(
    prepared: PreparedMarkdownExecution,
    spec_markdown: str,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    execution_context: dict[str, Any] | None = None,
    execution_files: list[dict[str, Any]] | None = None,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    primary_source_id: str | None = None,
    discover_workspace_files: bool = False,
    operation_id: str | None = None,
    response_id: str | None = None,
    trace_id: str | None = None,
    model: str | None = None,
    language: str = "auto",
    history: list[dict[str, Any]] | None = None,
    gen_report_base_url: str | None = None,
    gen_report_public_url: str | None = None,
    memory_context: MemoryContext | None = None,
) -> SelectedEngine:
    """Resolve the engine for a confirmed Markdown specification."""

    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=runtime_options,
        include_method_hub=False,
        execution_context=execution_context,
        execution_files=execution_files,
        organization_id=organization_id,
        workspace_id=workspace_id,
        primary_source_id=primary_source_id,
        discover_workspace_files=discover_workspace_files,
        operation_id=operation_id,
        response_id=response_id,
        trace_id=trace_id,
        model=model,
        language=language,
        history=history,
        gen_report_base_url=gen_report_base_url,
        gen_report_public_url=gen_report_public_url,
    )
    spec = _execution_spec_from_markdown(prepared, spec_markdown, runtime_options)
    return pipeline.select_engine(
        _confirmed_prepared_execution(prepared, spec),
        spec,
        memory_context or MemoryContext(),
    )


def execute_instant_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    selection: SelectedEngine | None = None,
    **runtime_context: Any,
) -> FinalResponse:
    """Select and execute immediately without creating a user-visible spec."""

    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=invocation.runtime_options,
        **runtime_context,
    )
    spec = ExecutionSpec(
        intent="unknown",
        objective=invocation.query.text,
        confirmed=True,
        engine_hint=ENGINE_ROUTE_MAP.get(invocation.runtime_options.engine or ""),
    )
    prepared = PreparedExecution(
        query=invocation.query,
        intent=spec.intent,
        spec=spec,
        session_context=invocation.session_context,
        user_context=invocation.user_context,
    )
    execution_kwargs: dict[str, Any] = {
        "memory_context": invocation.memory_context,
    }
    if selection is not None:
        execution_kwargs["selection"] = selection
    return pipeline.execute_confirmed_spec(prepared, spec, **execution_kwargs)


def select_instant_workflow(
    invocation: WorkflowInvocation,
    logger: RuntimeLogger,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    **runtime_context: Any,
) -> SelectedEngine:
    """Resolve the Instant engine without creating a user-visible spec."""

    pipeline = _create_pipeline(
        pipeline_factory,
        logger=logger,
        runtime_options=invocation.runtime_options,
        include_method_hub=False,
        **runtime_context,
    )
    spec = ExecutionSpec(
        intent="unknown",
        objective=invocation.query.text,
        confirmed=True,
        engine_hint=ENGINE_ROUTE_MAP.get(invocation.runtime_options.engine or ""),
    )
    prepared = PreparedExecution(
        query=invocation.query,
        intent=spec.intent,
        spec=spec,
        session_context=invocation.session_context,
        user_context=invocation.user_context,
    )
    return pipeline.select_engine(prepared, spec, invocation.memory_context)


def _execution_spec_from_markdown(
    prepared: PreparedMarkdownExecution,
    spec_markdown: str,
    runtime_options: WorkflowRuntimeOptions,
) -> ExecutionSpec:
    intent = runtime_options.engine or prepared.intent_analysis.intent
    if intent not in {"general", "reason", "report"}:
        intent = prepared.intent_analysis.intent
    return ExecutionSpec(
        intent=cast(Intent, intent),
        objective=validate_spec_markdown(spec_markdown),
        confirmed=True,
        engine_hint=ENGINE_ROUTE_MAP.get(runtime_options.engine or ""),
        preprocessing_steps=list(prepared.intent_analysis.preprocessing_steps),
    )


def _confirmed_prepared_execution(
    prepared: PreparedMarkdownExecution,
    spec: ExecutionSpec,
) -> PreparedExecution:
    return PreparedExecution(
        query=prepared.query,
        intent=spec.intent,
        spec=spec,
        session_context=prepared.session_context,
        user_context=prepared.user_context,
        intent_analysis=prepared.intent_analysis,
        run_artifact=prepared.run_artifact,
        run_artifact_id=prepared.run_artifact_id,
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
            PreprocessingStep(**item) for item in payload.get("preprocessing_steps", [])
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
