"""Stateless operation endpoints for the Data Intelligence runtime."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from data_intelligence_api.application.runtime_operations import (
    execute_direct_report,
    execute_spec,
    prepare_spec,
    revise_spec,
    stream_report_events,
)
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    default_pipeline_factory,
)
from data_intelligence_api.http.schemas.runtime_operations import (
    DirectExecuteRequest,
    ExecuteRequest,
    OperationEnvelope,
    PrepareSpecRequest,
    ReviseSpecRequest,
    RuntimeErrorResponse,
)
from data_intelligence_api.http.streaming import chunk_text, encode_sse
from data_intelligence_api.infrastructure.config.settings import ApiSettings


logger = logging.getLogger(__name__)


def _authorize_service(
    settings: ApiSettings,
    authorization: str | None,
    consumer_service: str | None,
) -> None:
    if settings.runtime_service_token is None:
        raise HTTPException(
            status_code=503,
            detail="Runtime service authentication is not configured.",
        )
    expected = f"Bearer {settings.runtime_service_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Runtime service authentication failed.")
    if consumer_service != settings.runtime_consumer_service:
        raise HTTPException(status_code=403, detail="Runtime consumer service is not allowed.")


def _error_response(
    request: OperationEnvelope,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    error = RuntimeErrorResponse(
        code=code,
        message=message,
        retryable=retryable,
        operation_id=request.operation_id,
        trace_id=request.trace_id,
    )
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


def create_runtime_operations_router(
    *,
    settings: ApiSettings,
    pipeline_factory: PipelineFactory,
) -> APIRouter:
    router = APIRouter(tags=["runtime-operations"])

    @router.post("/v1/specs:prepare")
    async def prepare_runtime_spec(
        request: PrepareSpecRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
        consumer_service: str | None = Header(default=None, alias="X-Consumer-Service"),
    ):
        _authorize_service(settings, authorization, consumer_service)
        try:
            return prepare_spec(
                request,
                settings=settings,
                pipeline_factory=pipeline_factory,
            )
        except ValueError:
            return _error_response(
                request,
                status_code=422,
                code="validation_error",
                message="The runtime operation payload is invalid.",
                retryable=False,
            )
        except Exception:
            return _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="The runtime could not prepare the execution spec.",
                retryable=False,
            )

    @router.post("/v1/specs:revise")
    async def revise_runtime_spec(
        request: ReviseSpecRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
        consumer_service: str | None = Header(default=None, alias="X-Consumer-Service"),
    ):
        _authorize_service(settings, authorization, consumer_service)
        try:
            return revise_spec(request, pipeline_factory=pipeline_factory)
        except ValueError:
            return _error_response(
                request,
                status_code=422,
                code="validation_error",
                message="The revised execution spec is invalid.",
                retryable=False,
            )
        except Exception:
            return _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="The runtime could not revise the execution spec.",
                retryable=False,
            )

    @router.post("/v1/executions:stream")
    async def stream_runtime_execution(
        request: ExecuteRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
        consumer_service: str | None = Header(default=None, alias="X-Consumer-Service"),
    ) -> StreamingResponse:
        _authorize_service(settings, authorization, consumer_service)

        async def event_stream() -> AsyncIterator[str]:
            if (
                request.runtime_input.runtime_options.engine == "report"
                and pipeline_factory is default_pipeline_factory
            ):
                try:
                    async for event in stream_report_events(
                        request,
                        instruction=request.spec_markdown,
                        settings=settings,
                    ):
                        yield encode_sse(event["type"], event)
                except Exception:
                    logger.exception(
                        "Runtime report stream failed operation_id=%s response_id=%s",
                        request.operation_id,
                        request.response_id,
                    )
                    yield encode_sse(
                        "runtime.failed",
                        {
                            "type": "runtime.failed",
                            "operation_id": request.operation_id,
                            "response_id": request.response_id,
                            "payload": {
                                "code": "execution_failed",
                                "message": "The runtime execution failed.",
                                "retryable": False,
                            },
                        },
                    )
                return
            try:
                result = execute_spec(
                    request,
                    settings=settings,
                    pipeline_factory=pipeline_factory,
                )
            except Exception:
                logger.exception(
                    "Runtime execution failed operation_id=%s response_id=%s",
                    request.operation_id,
                    request.response_id,
                )
                yield encode_sse(
                    "runtime.failed",
                    {
                        "type": "runtime.failed",
                        "operation_id": request.operation_id,
                        "response_id": request.response_id,
                        "payload": {
                            "code": "execution_failed",
                            "message": "The runtime execution failed.",
                            "retryable": False,
                        },
                    },
                )
                return

            for delta in chunk_text(result.answer):
                yield encode_sse(
                    "runtime.output_text.delta",
                    {
                        "type": "runtime.output_text.delta",
                        "operation_id": request.operation_id,
                        "response_id": request.response_id,
                        "payload": {"delta": delta},
                    },
                )
            yield encode_sse(
                "runtime.completed",
                {
                    "type": "runtime.completed",
                    "operation_id": request.operation_id,
                    "response_id": request.response_id,
                    "payload": {
                        "output_text": result.answer,
                        "evidence": (
                            asdict(result.evidence) if result.evidence is not None else None
                        ),
                        "metadata": dict(result.metadata),
                    },
                },
            )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.post("/v1/executions:run-stream")
    async def stream_direct_runtime_execution(
        request: DirectExecuteRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
        consumer_service: str | None = Header(default=None, alias="X-Consumer-Service"),
    ) -> StreamingResponse:
        _authorize_service(settings, authorization, consumer_service)

        async def event_stream() -> AsyncIterator[str]:
            if pipeline_factory is default_pipeline_factory:
                try:
                    async for event in stream_report_events(
                        request,
                        instruction=request.runtime_input.input,
                        settings=settings,
                    ):
                        yield encode_sse(event["type"], event)
                except Exception:
                    logger.exception(
                        "Runtime direct report stream failed operation_id=%s response_id=%s",
                        request.operation_id,
                        request.response_id,
                    )
                    yield encode_sse(
                        "runtime.failed",
                        {
                            "type": "runtime.failed",
                            "operation_id": request.operation_id,
                            "response_id": request.response_id,
                            "payload": {
                                "code": "execution_failed",
                                "message": "The runtime execution failed.",
                                "retryable": False,
                            },
                        },
                    )
                return
            try:
                result = execute_direct_report(
                    request,
                    settings=settings,
                    pipeline_factory=pipeline_factory,
                )
            except Exception:
                logger.exception(
                    "Runtime direct execution failed operation_id=%s response_id=%s",
                    request.operation_id,
                    request.response_id,
                )
                yield encode_sse(
                    "runtime.failed",
                    {
                        "type": "runtime.failed",
                        "operation_id": request.operation_id,
                        "response_id": request.response_id,
                        "payload": {
                            "code": "execution_failed",
                            "message": "The runtime execution failed.",
                            "retryable": False,
                        },
                    },
                )
                return

            for delta in chunk_text(result.answer):
                yield encode_sse(
                    "runtime.output_text.delta",
                    {
                        "type": "runtime.output_text.delta",
                        "operation_id": request.operation_id,
                        "response_id": request.response_id,
                        "payload": {"delta": delta},
                    },
                )
            yield encode_sse(
                "runtime.completed",
                {
                    "type": "runtime.completed",
                    "operation_id": request.operation_id,
                    "response_id": request.response_id,
                    "payload": {
                        "output_text": result.answer,
                        "evidence": (
                            asdict(result.evidence) if result.evidence is not None else None
                        ),
                        "metadata": dict(result.metadata),
                    },
                },
            )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return router
