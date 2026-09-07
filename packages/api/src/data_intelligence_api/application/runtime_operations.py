"""Stateless adapters for Data Intelligence runtime operations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from data_intelligence_sdk.core.types import FinalResponse
from data_intelligence_sdk.registry.engine_registry import SelectedEngine
from data_intelligence_sdk.runtime.logger import ConsoleRuntimeLogger, RuntimeLogger

from data_intelligence_api.application.runtime_capabilities import (
    resolve_runtime_options,
)
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    build_workflow_invocation,
    default_pipeline_factory,
    execute_instant_workflow,
    execute_prepared_markdown_workflow,
    prepare_workflow,
    prepared_markdown_from_payload,
    prepared_markdown_to_payload,
    revise_markdown_workflow,
    select_instant_workflow,
    select_prepared_markdown_engine,
    stream_instant_workflow,
    stream_prepared_markdown_workflow,
)
from data_intelligence_api.http.schemas.runtime_inputs import WorkflowRequest
from data_intelligence_api.http.schemas.runtime_operations import (
    InstantExecutionRequest,
    PrepareSpecRequest,
    PrepareSpecResponse,
    ReviseSpecRequest,
    ReviseSpecResponse,
    RuntimeInput,
    ThinkingExecutionRequest,
)
from data_intelligence_api.infrastructure.memory import parse_upstream_memory_context
from data_intelligence_api.infrastructure.workflow.gen_report_engine import (
    GenReportEngine,
)


def _to_workflow_request(runtime_input: RuntimeInput) -> WorkflowRequest:
    return WorkflowRequest(
        input=runtime_input.input,
        session_id=runtime_input.session_id,
        model=runtime_input.model,
        language=runtime_input.language,
        history=runtime_input.history,
        organization_id=runtime_input.organization_id,
        user_id=runtime_input.user_id,
        workspace_id=runtime_input.workspace_id,
        uploaded_files=runtime_input.uploaded_files,
        runtime_options=runtime_input.runtime_options,
        execution_context=runtime_input.execution_context,
        execution_files=runtime_input.execution_files,
        primary_source_id=runtime_input.primary_source_id,
        selected_files=runtime_input.selected_files,
        internal_memory_context=runtime_input.internal_memory_context,
    )


def _logger_or_default(logger: RuntimeLogger | None) -> RuntimeLogger:
    return logger or ConsoleRuntimeLogger()


def _discover_workspace_files(runtime_input: RuntimeInput) -> bool:
    if runtime_input.discover_workspace_files is not None:
        return runtime_input.discover_workspace_files
    return runtime_input.runtime_options.method_hub_enabled is not False


async def stream_report_events(
    request: ThinkingExecutionRequest | InstantExecutionRequest,
    *,
    instruction: str,
    settings: object,
    user_authorization: str | None = None,
    engine_factory: Callable[..., GenReportEngine] = GenReportEngine,
) -> AsyncIterator[dict[str, Any]]:
    runtime_input = request.runtime_input
    if runtime_input.execution_context is None:
        raise ValueError("report execution requires an execution context")
    if not runtime_input.organization_id or not runtime_input.workspace_id:
        raise ValueError("report execution requires organization and workspace scope")
    engine = engine_factory(
        getattr(settings, "gen_report_api_url"),
        public_base_url=getattr(settings, "gen_report_public_url", None),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        user_authorization=user_authorization,
        model=runtime_input.model,
        language=runtime_input.language,
        history=[item.model_dump(mode="json") for item in runtime_input.history],
        execution_context=runtime_input.execution_context.model_dump(mode="json"),
        execution_files=[
            item.model_dump(mode="json") for item in runtime_input.execution_files
        ],
        workspace_id=runtime_input.workspace_id,
        primary_source_id=runtime_input.primary_source_id,
        primary_source_ids=runtime_input.primary_source_ids,
        all_inputs_primary=runtime_input.all_inputs_primary,
        discover_workspace_files=_discover_workspace_files(runtime_input),
        workspace_discovery_instruction=runtime_input.workspace_discovery_instruction,
        selected_files=(
            runtime_input.selected_files.model_dump(
                mode="json",
                exclude_defaults=True,
            )
            if runtime_input.selected_files is not None
            else None
        ),
        workflow=runtime_input.runtime_options.workflow,
    )
    latest_usage: dict[str, Any] | None = None
    async for event in engine.stream_events(
        instruction=instruction,
        organization_id=runtime_input.organization_id,
    ):
        event_type = event.get("type")
        payload = event.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        if event_type == "report.status":
            runtime_type = "runtime.progress"
            payload = {
                **payload,
                "event_type": event_type,
                "status": str(payload.get("status") or "running"),
            }
        elif event_type in {
            "report.tool.started",
            "report.tool.completed",
            "report.tool.failed",
        }:
            runtime_type = "runtime.progress"
            status_by_type = {
                "report.tool.started": "started",
                "report.tool.completed": "completed",
                "report.tool.failed": "failed",
            }
            tool_name = str(payload.get("tool_name") or "tool")
            payload = {
                **payload,
                "event_type": event_type,
                "phase": "tool",
                "status": str(payload.get("status") or status_by_type[event_type]),
                "label": str(payload.get("label") or tool_name),
            }
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                payload.setdefault("event_id", event_id)
        elif event_type == "report.inputs.selected":
            runtime_type = "runtime.progress"
            payload = {
                **payload,
                "event_type": event_type,
                "phase": "discovery",
                "status": "completed",
            }
        elif event_type == "report.output_text.delta":
            runtime_type = "runtime.output_text.delta"
            payload = {"delta": str(payload.get("delta") or "")}
        elif event_type == "report.usage":
            runtime_type = "runtime.usage"
            latest_usage = dict(payload)
        elif event_type == "report.failed":
            runtime_type = "runtime.failed"
        elif event_type == "report.completed":
            runtime_type = "runtime.completed"
            completion_usage = payload.get("usage")
            metadata = {
                "engine_name": "report",
                "route": "gen_report",
                "artifacts": list(payload.get("artifacts") or []),
                "usage": (
                    completion_usage
                    if isinstance(completion_usage, dict)
                    else latest_usage
                ),
            }
            payload = {
                "output_text": str(payload.get("output_text") or ""),
                "evidence": None,
                "metadata": metadata,
            }
        else:
            continue
        yield {
            "type": runtime_type,
            "operation_id": request.operation_id,
            "response_id": request.response_id,
            "payload": payload,
        }


def prepare_spec(
    request: PrepareSpecRequest,
    *,
    settings: object,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
) -> PrepareSpecResponse:
    invocation = build_workflow_invocation(
        _to_workflow_request(request.runtime_input),
        settings.data_corpus_root,  # type: ignore[attr-defined]
        method_hub_default_enabled=settings.method_hub_default_enabled,  # type: ignore[attr-defined]
        memory_context=parse_upstream_memory_context(request.memory_context),
    )
    prepared = prepare_workflow(
        invocation,
        _logger_or_default(logger),
        pipeline_factory,
    )
    return PrepareSpecResponse(
        schema_version=request.schema_version,
        operation_id=request.operation_id,
        attempt=request.attempt,
        response_id=request.response_id,
        trace_id=request.trace_id,
        prepared_execution=prepared_markdown_to_payload(prepared),
        spec_markdown=prepared.spec_markdown,
        intent={
            "value": prepared.intent_analysis.intent,
            "catalog_intent_id": prepared.intent_analysis.catalog_intent_id,
        },
        metadata={},
    )


def revise_spec(
    request: ReviseSpecRequest,
    *,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
) -> ReviseSpecResponse:
    prepared = prepared_markdown_from_payload(
        request.prepared_execution,
        request.current_spec_markdown,
    )
    runtime_options = resolve_runtime_options(
        request.runtime_input.runtime_options,
        default_enabled=False,
    )
    revised = revise_markdown_workflow(
        prepared,
        request.revised_spec_markdown,
        _logger_or_default(logger),
        runtime_options,
        pipeline_factory,
    )
    return ReviseSpecResponse(
        schema_version=request.schema_version,
        operation_id=request.operation_id,
        attempt=request.attempt,
        response_id=request.response_id,
        trace_id=request.trace_id,
        spec_markdown=revised,
        metadata={},
    )


def execute_thinking(
    request: ThinkingExecutionRequest,
    *,
    settings: object | None = None,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
    selection: SelectedEngine | None = None,
    user_authorization: str | None = None,
) -> FinalResponse:
    prepared = prepared_markdown_from_payload(
        request.prepared_execution,
        request.spec_markdown,
    )
    runtime_options = resolve_runtime_options(
        request.runtime_input.runtime_options,
        default_enabled=False,
    )
    return execute_prepared_markdown_workflow(
        prepared,
        request.spec_markdown,
        _logger_or_default(logger),
        runtime_options,
        pipeline_factory,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=(
            getattr(settings, "gen_report_api_url", None)
            if settings is not None
            else None
        ),
        gen_report_public_url=(
            getattr(settings, "gen_report_public_url", None)
            if settings is not None
            else None
        ),
        memory_context=parse_upstream_memory_context(request.memory_context),
        selection=selection,
        user_authorization=user_authorization,
    )


def stream_thinking(
    request: ThinkingExecutionRequest,
    *,
    settings: object | None = None,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
    selection: SelectedEngine | None = None,
    user_authorization: str | None = None,
) -> Iterator[str | FinalResponse]:
    """Stream a confirmed Thinking execution from the runtime engine."""

    prepared = prepared_markdown_from_payload(
        request.prepared_execution,
        request.spec_markdown,
    )
    runtime_options = resolve_runtime_options(
        request.runtime_input.runtime_options,
        default_enabled=False,
    )
    yield from stream_prepared_markdown_workflow(
        prepared,
        request.spec_markdown,
        _logger_or_default(logger),
        runtime_options,
        pipeline_factory,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=(
            getattr(settings, "gen_report_api_url", None)
            if settings is not None
            else None
        ),
        gen_report_public_url=(
            getattr(settings, "gen_report_public_url", None)
            if settings is not None
            else None
        ),
        memory_context=parse_upstream_memory_context(request.memory_context),
        selection=selection,
        user_authorization=user_authorization,
    )


def select_thinking_engine(
    request: ThinkingExecutionRequest,
    *,
    settings: object,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
) -> SelectedEngine:
    """Select the engine for a confirmed Thinking specification exactly once."""

    prepared = prepared_markdown_from_payload(
        request.prepared_execution,
        request.spec_markdown,
    )
    runtime_options = resolve_runtime_options(
        request.runtime_input.runtime_options,
        default_enabled=False,
    )
    return select_prepared_markdown_engine(
        prepared,
        request.spec_markdown,
        _logger_or_default(logger),
        runtime_options,
        pipeline_factory,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=getattr(settings, "gen_report_api_url", None),
        gen_report_public_url=getattr(settings, "gen_report_public_url", None),
        memory_context=parse_upstream_memory_context(request.memory_context),
    )


def execute_instant(
    request: InstantExecutionRequest,
    *,
    settings: object,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
    selection: SelectedEngine | None = None,
    user_authorization: str | None = None,
) -> FinalResponse:
    """Run an Instant request without preparing a Markdown specification."""

    invocation = build_workflow_invocation(
        _to_workflow_request(request.runtime_input),
        settings.data_corpus_root,  # type: ignore[attr-defined]
        method_hub_default_enabled=settings.method_hub_default_enabled,  # type: ignore[attr-defined]
        memory_context=parse_upstream_memory_context(request.memory_context),
    )
    return execute_instant_workflow(
        invocation,
        _logger_or_default(logger),
        pipeline_factory,
        selection=selection,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=getattr(settings, "gen_report_api_url", None),
        gen_report_public_url=getattr(settings, "gen_report_public_url", None),
        user_authorization=user_authorization,
    )


def stream_instant(
    request: InstantExecutionRequest,
    *,
    settings: object,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
    selection: SelectedEngine | None = None,
    user_authorization: str | None = None,
) -> Iterator[str | FinalResponse]:
    """Stream an Instant execution from the runtime engine."""

    invocation = build_workflow_invocation(
        _to_workflow_request(request.runtime_input),
        settings.data_corpus_root,  # type: ignore[attr-defined]
        method_hub_default_enabled=settings.method_hub_default_enabled,  # type: ignore[attr-defined]
        memory_context=parse_upstream_memory_context(request.memory_context),
    )
    yield from stream_instant_workflow(
        invocation,
        _logger_or_default(logger),
        pipeline_factory,
        selection=selection,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=getattr(settings, "gen_report_api_url", None),
        gen_report_public_url=getattr(settings, "gen_report_public_url", None),
        user_authorization=user_authorization,
    )


def select_instant_engine(
    request: InstantExecutionRequest,
    *,
    settings: object,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
) -> SelectedEngine:
    """Select the engine used by an Instant operation exactly once."""

    invocation = build_workflow_invocation(
        _to_workflow_request(request.runtime_input),
        settings.data_corpus_root,  # type: ignore[attr-defined]
        method_hub_default_enabled=settings.method_hub_default_enabled,  # type: ignore[attr-defined]
        memory_context=parse_upstream_memory_context(request.memory_context),
    )
    return select_instant_workflow(
        invocation,
        _logger_or_default(logger),
        pipeline_factory,
        execution_context=(
            request.runtime_input.execution_context.model_dump(mode="json")
            if request.runtime_input.execution_context is not None
            else None
        ),
        execution_files=[
            item.model_dump(mode="json")
            for item in request.runtime_input.execution_files
        ],
        primary_source_id=request.runtime_input.primary_source_id,
        organization_id=request.runtime_input.organization_id,
        workspace_id=request.runtime_input.workspace_id,
        discover_workspace_files=_discover_workspace_files(request.runtime_input),
        operation_id=request.operation_id,
        response_id=request.response_id,
        trace_id=request.trace_id,
        model=request.runtime_input.model,
        language=request.runtime_input.language,
        history=[
            item.model_dump(mode="json") for item in request.runtime_input.history
        ],
        gen_report_base_url=getattr(settings, "gen_report_api_url", None),
        gen_report_public_url=getattr(settings, "gen_report_public_url", None),
    )
