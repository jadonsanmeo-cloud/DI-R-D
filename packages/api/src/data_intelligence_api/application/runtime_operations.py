"""Stateless adapters for Data Intelligence runtime operations."""

from __future__ import annotations

from data_intelligence_api.application.runtime_capabilities import (
    resolve_runtime_options,
)
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    build_workflow_invocation,
    default_pipeline_factory,
    execute_prepared_markdown_workflow,
    prepare_workflow,
    prepared_markdown_from_payload,
    prepared_markdown_to_payload,
    revise_markdown_workflow,
)
from data_intelligence_api.http.schemas.runtime_inputs import WorkflowRequest
from data_intelligence_api.http.schemas.runtime_operations import (
    ExecuteRequest,
    PrepareSpecRequest,
    PrepareSpecResponse,
    ReviseSpecRequest,
    ReviseSpecResponse,
    RuntimeInput,
)
from data_intelligence_sdk.core.types import FinalResponse
from data_intelligence_sdk.runtime.logger import ConsoleRuntimeLogger, RuntimeLogger


def _to_workflow_request(runtime_input: RuntimeInput) -> WorkflowRequest:
    return WorkflowRequest(
        input=runtime_input.input,
        session_id=runtime_input.session_id,
        uploaded_files=runtime_input.uploaded_files,
        runtime_options=runtime_input.runtime_options,
        execution_context=runtime_input.execution_context,
        execution_files=runtime_input.execution_files,
    )


def _logger_or_default(logger: RuntimeLogger | None) -> RuntimeLogger:
    return logger or ConsoleRuntimeLogger()


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


def execute_spec(
    request: ExecuteRequest,
    *,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    logger: RuntimeLogger | None = None,
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
    )
