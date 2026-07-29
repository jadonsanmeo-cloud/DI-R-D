"""Resumable streaming Responses API with durable spec confirmation."""

from __future__ import annotations

import asyncio
import logging
import queue
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from threading import Thread
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse

from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.persistence.artifact_history import (
    ArtifactHistoryReader,
)
from data_intelligence_api.application.runtime_capabilities import (
    MethodHubUnavailableError,
)
from data_intelligence_api.application.ports.run_repository import RunRepository
from data_intelligence_api.domain.workflow import WorkflowRuntimeOptions
from data_intelligence_api.domain.runs import (
    RunConflictError,
    RunExpiredError,
    RunNotFoundError,
)
from data_intelligence_api.infrastructure.persistence.run_store import (
    hash_confirmation_token,
)
from data_intelligence_api.http.schemas.responses import (
    CreateResponseRequest,
    ResponseDecisionRequest,
    ResponseHistoryDetail,
    ResponseHistorySummary,
    RuntimeOptionsRequest,
)
from data_intelligence_api.http.streaming import (
    PipelineLogMessage,
    QueueRuntimeLogger,
    WorkflowFailedMessage,
    WorkflowResultMessage,
    chunk_text,
    encode_sse,
)
from data_intelligence_api.application.workflow import (
    PipelineFactory,
    SourceValidationError,
    build_workflow_invocation,
    default_pipeline_factory,
    execute_prepared_markdown_workflow,
    markdown_spec_from_payload,
    markdown_spec_to_payload,
    prepare_workflow,
    prepared_markdown_from_payload,
    prepared_markdown_to_payload,
    revise_markdown_workflow,
)
from data_intelligence_api.application.query_orchestrator import (
    DelegateToDataFlow,
    DirectGeneralAnswer,
)
from data_intelligence_sdk.core.types import FinalResponse

logger = logging.getLogger(__name__)


def _failed_payload(response_id: str, message: WorkflowFailedMessage) -> dict:
    return {
        "type": "response.failed",
        "response_id": response_id,
        "response": {"id": response_id, "status": "failed"},
        "error": {"code": message.code, "message": message.message},
    }


def _run_worker(
    operation: Callable[[], object],
    messages: queue.Queue[object],
    *,
    error_code: str,
    safe_error: str,
) -> None:
    try:
        messages.put(WorkflowResultMessage(result=operation()))
    except MethodHubUnavailableError:
        logger.exception("Method Hub is unavailable")
        messages.put(
            WorkflowFailedMessage(
                code="method_hub_unavailable",
                message=("Method Hub is enabled for this request but is unavailable."),
            )
        )
    except Exception:
        logger.exception("Data Intelligence workflow phase failed")
        messages.put(WorkflowFailedMessage(code=error_code, message=safe_error))


async def _stream_operation(
    *,
    response_id: str,
    messages: queue.Queue[object],
    operation: Callable[[], object],
    settings: ApiSettings,
    error_code: str,
    safe_error: str,
) -> AsyncIterator[object]:
    Thread(
        target=_run_worker,
        args=(operation, messages),
        kwargs={"error_code": error_code, "safe_error": safe_error},
        daemon=True,
    ).start()
    deadline = asyncio.get_running_loop().time() + settings.pipeline_timeout_seconds
    while True:
        if asyncio.get_running_loop().time() >= deadline:
            yield WorkflowFailedMessage(
                code="pipeline_execution_timeout",
                message="The data intelligence workflow timed out.",
            )
            return
        try:
            message = messages.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.01)
            continue
        if isinstance(message, PipelineLogMessage):
            yield encode_sse(
                message.event,
                {"type": message.event, "response_id": response_id, **message.payload},
            )
            continue
        yield message
        return


def _confirmation_payload(
    *,
    response_id: str,
    revision: int,
    token: str,
    intent_payload: dict,
    spec_markdown: str,
    expires_at: datetime,
) -> dict:
    return {
        "type": "response.requires_confirmation",
        "response_id": response_id,
        "revision": revision,
        "confirmation_token": token,
        "intent": intent_payload,
        "spec_markdown": spec_markdown,
        "expires_at": expires_at.isoformat(),
    }


def _raise_store_error(error: Exception) -> None:
    if isinstance(error, RunNotFoundError):
        raise HTTPException(
            status_code=404, detail="Response was not found."
        ) from error
    if isinstance(error, RunExpiredError):
        raise HTTPException(status_code=410, detail=str(error)) from error
    if isinstance(error, RunConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise error


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _history_title(run) -> str:
    input_text = run.request_payload.get("input")
    if isinstance(input_text, str) and input_text.strip():
        return input_text.strip()
    objective = run.spec_payload.get("objective")
    if isinstance(objective, str) and objective.strip():
        return objective.strip()
    markdown = run.spec_payload.get("spec_markdown")
    if isinstance(markdown, str) and markdown.strip():
        for line in markdown.splitlines():
            if line.strip() and not line.startswith("#"):
                return line.strip()[:120]
    return "Untitled task"


def _history_summary(run) -> ResponseHistorySummary:
    output_preview = run.output_text.strip()[:160] if run.output_text else None
    return ResponseHistorySummary(
        response_id=run.response_id,
        title=_history_title(run),
        status=run.status,
        output_preview=output_preview,
        created_at=_isoformat(run.created_at),
        updated_at=_isoformat(run.updated_at),
        completed_at=_isoformat(run.completed_at),
    )


def _history_detail(
    run,
    artifact_history: ArtifactHistoryReader,
) -> ResponseHistoryDetail:
    spec_payload = dict(run.spec_payload)
    input_text = run.request_payload.get("input")
    method_hub_value = run.request_payload.get("runtime_options", {}).get(
        "method_hub_enabled"
    )
    engine_value = run.request_payload.get("runtime_options", {}).get("engine")
    restored_engine = (
        engine_value
        if engine_value in {"auto", "general", "reason", "report"}
        else None
    )
    metadata = run.response_metadata or {}
    return ResponseHistoryDetail(
        response_id=run.response_id,
        status=run.status,
        input=input_text if isinstance(input_text, str) else "",
        spec=spec_payload,
        runtime_options=RuntimeOptionsRequest(
            method_hub_enabled=(
                method_hub_value if isinstance(method_hub_value, bool) else False
            ),
            engine=restored_engine,
        ),
        output_text=run.output_text,
        evidence=run.evidence,
        metadata=metadata,
        events=artifact_history.read_pipeline_events(
            metadata.get("artifact_ref"),
            evidence_present=run.evidence is not None,
        ),
        error=(
            {
                "code": run.error_code or "response_failed",
                "message": run.error_message or "The response failed.",
            }
            if run.error_code or run.error_message
            else None
        ),
        created_at=_isoformat(run.created_at),
        updated_at=_isoformat(run.updated_at),
        completed_at=_isoformat(run.completed_at),
    )


def _runtime_options_from_payload(
    payload: dict,
    *,
    default_enabled: bool,
) -> WorkflowRuntimeOptions:
    raw_options = payload.get("runtime_options", {})
    raw_value = raw_options.get("method_hub_enabled")
    raw_engine = raw_options.get("engine")
    engine = raw_engine if raw_engine in {"general", "reason", "report"} else None
    return WorkflowRuntimeOptions(
        method_hub_enabled=(
            raw_value if isinstance(raw_value, bool) else default_enabled
        ),
        engine=engine,
    )


def create_responses_router(
    settings: ApiSettings,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    run_repository: RunRepository | None = None,
    query_orchestrator: object | None = None,
) -> APIRouter:
    if run_repository is None:
        raise ValueError(
            "run_repository is required for resumable Responses workflows."
        )
    if query_orchestrator is None:
        raise ValueError("query_orchestrator is required for Responses workflows.")
    router = APIRouter()
    artifact_history = ArtifactHistoryReader(settings.artifact_root)

    @router.post("/api/v1/responses")
    async def create_response(payload: CreateResponseRequest) -> StreamingResponse:
        try:
            invocation = build_workflow_invocation(
                payload,
                settings.data_corpus_root,
                method_hub_default_enabled=settings.method_hub_default_enabled,
            )
        except SourceValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        response_id = f"resp_{uuid.uuid4().hex}"
        confirmation_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.spec_confirmation_ttl_seconds
        )
        messages: queue.Queue[object] = queue.Queue()
        event_logger = QueueRuntimeLogger(messages)

        async def event_stream() -> AsyncIterator[str]:
            yield encode_sse(
                "response.created",
                {
                    "type": "response.created",
                    "response_id": response_id,
                    "response": {"id": response_id, "status": "in_progress"},
                },
            )
            try:
                route = await query_orchestrator.route(  # type: ignore[attr-defined]
                    invocation.query,
                    invocation.session_context,
                )
            except Exception as exc:
                logger.error(
                    "Query orchestration failed error_type=%s",
                    type(exc).__name__,
                )
                failure = WorkflowFailedMessage(
                    code="query_orchestration_failed",
                    message="The request could not be routed.",
                )
                yield encode_sse(
                    "response.failed",
                    _failed_payload(response_id, failure),
                )
                return

            if isinstance(route, DirectGeneralAnswer):
                output_text = route.text
                for delta in chunk_text(output_text):
                    yield encode_sse(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "response_id": response_id,
                            "delta": delta,
                        },
                    )
                yield encode_sse(
                    "response.output_text.done",
                    {
                        "type": "response.output_text.done",
                        "response_id": response_id,
                        "text": output_text,
                    },
                )
                yield encode_sse(
                    "response.completed",
                    {
                        "type": "response.completed",
                        "response_id": response_id,
                        "response": {
                            "id": response_id,
                            "status": "completed",
                            "output_text": output_text,
                        },
                        "evidence": None,
                        "metadata": {"route": "general_direct"},
                    },
                )
                return

            if not isinstance(route, DelegateToDataFlow):
                failure = WorkflowFailedMessage(
                    code="query_orchestration_protocol_error",
                    message="The request router returned an invalid result.",
                )
                yield encode_sse(
                    "response.failed",
                    _failed_payload(response_id, failure),
                )
                return

            async for message in _stream_operation(
                response_id=response_id,
                messages=messages,
                operation=lambda: prepare_workflow(
                    invocation, event_logger, pipeline_factory
                ),
                settings=settings,
                error_code="pipeline_preparation_failed",
                safe_error="The execution spec could not be prepared.",
            ):
                if isinstance(message, str):
                    yield message
                elif isinstance(message, WorkflowFailedMessage):
                    yield encode_sse(
                        "response.failed", _failed_payload(response_id, message)
                    )
                elif isinstance(message, WorkflowResultMessage):
                    prepared = message.result
                    spec_markdown = prepared.spec_markdown
                    intent_payload = {
                        "value": prepared.intent_analysis.intent,
                        "catalog_intent_id": prepared.intent_analysis.catalog_intent_id,
                    }
                    run_repository.create_pending(
                        response_id=response_id,
                        token_hash=hash_confirmation_token(confirmation_token),
                        request_payload={
                            **payload.model_dump(mode="json"),
                            "runtime_options": {
                                "method_hub_enabled": (
                                    invocation.runtime_options.method_hub_enabled
                                ),
                                "engine": invocation.runtime_options.engine or "auto",
                            },
                        },
                        prepared_execution=prepared_markdown_to_payload(prepared),
                        intent_payload=intent_payload,
                        spec_payload=markdown_spec_to_payload(spec_markdown),
                        user_id=payload.user_id,
                        session_id=payload.session_id,
                        expires_at=expires_at,
                    )
                    confirmation = _confirmation_payload(
                        response_id=response_id,
                        revision=1,
                        token=confirmation_token,
                        intent_payload=intent_payload,
                        spec_markdown=spec_markdown,
                        expires_at=expires_at,
                    )
                    yield encode_sse("response.requires_confirmation", confirmation)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.get("/api/v1/responses")
    async def list_response_history(
        session_id: str = Query(min_length=1),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        runs = run_repository.list_for_session(session_id, limit=limit)
        return JSONResponse(
            {"items": [_history_summary(run).model_dump(mode="json") for run in runs]}
        )

    @router.get("/api/v1/responses/{response_id}/history")
    async def get_response_history(
        response_id: str,
        session_id: str = Query(min_length=1),
    ) -> JSONResponse:
        try:
            run = run_repository.get_for_session(response_id, session_id)
        except Exception as error:
            _raise_store_error(error)
        return JSONResponse(
            _history_detail(run, artifact_history).model_dump(mode="json")
        )

    @router.delete("/api/v1/responses/{response_id}", status_code=204)
    async def delete_response_history(
        response_id: str,
        session_id: str = Query(min_length=1),
    ) -> Response:
        try:
            run_repository.delete_for_session(response_id, session_id)
        except Exception as error:
            _raise_store_error(error)
        return Response(status_code=204)

    @router.get("/api/v1/responses/{response_id}")
    async def get_response(
        response_id: str,
        confirmation_token: str = Header(alias="X-Confirmation-Token"),
    ) -> JSONResponse:
        try:
            run = run_repository.get_authorized(response_id, confirmation_token)
        except Exception as error:
            _raise_store_error(error)
        return JSONResponse(
            {
                "response_id": run.response_id,
                "status": run.status,
                "revision": run.current_revision,
                "intent": run.intent_payload,
                "spec_markdown": markdown_spec_from_payload(run.spec_payload),
                "runtime_options": {
                    "method_hub_enabled": _runtime_options_from_payload(
                        run.request_payload,
                        default_enabled=settings.method_hub_default_enabled,
                    ).method_hub_enabled,
                    "engine": _runtime_options_from_payload(
                        run.request_payload,
                        default_enabled=settings.method_hub_default_enabled,
                    ).engine
                    or "auto",
                },
                "expires_at": run.expires_at.isoformat(),
                "error": (
                    {"code": run.error_code, "message": run.error_message}
                    if run.error_code
                    else None
                ),
            }
        )

    @router.post("/api/v1/responses/{response_id}/decision")
    async def decide_response(
        response_id: str,
        decision: ResponseDecisionRequest,
        confirmation_token: str = Header(alias="X-Confirmation-Token"),
    ) -> StreamingResponse:
        if (
            decision.action == "revise"
            and decision.revision > settings.max_spec_revision_rounds
        ):
            raise HTTPException(
                status_code=409, detail="Maximum spec revisions reached."
            )
        target_status = "revising" if decision.action == "revise" else "executing"
        try:
            run = run_repository.claim(
                response_id,
                confirmation_token,
                revision=decision.revision,
                target_status=target_status,
            )
        except Exception as error:
            _raise_store_error(error)

        current_markdown = markdown_spec_from_payload(run.spec_payload)
        prepared = prepared_markdown_from_payload(
            run.prepared_execution,
            current_markdown,
        )
        runtime_options = _runtime_options_from_payload(
            run.request_payload,
            default_enabled=settings.method_hub_default_enabled,
        )
        messages: queue.Queue[object] = queue.Queue()
        event_logger = QueueRuntimeLogger(messages)

        if decision.action == "revise":
            requested_markdown = decision.spec_markdown or ""

            async def revision_stream() -> AsyncIterator[str]:
                def execute_revision() -> str:
                    return revise_markdown_workflow(
                        prepared,
                        requested_markdown,
                        event_logger,
                        runtime_options,
                        pipeline_factory,
                    )

                async for message in _stream_operation(
                    response_id=response_id,
                    messages=messages,
                    operation=execute_revision,
                    settings=settings,
                    error_code="spec_revision_failed",
                    safe_error="The execution spec could not be revised.",
                ):
                    if isinstance(message, str):
                        yield message
                    elif isinstance(message, WorkflowFailedMessage):
                        run_repository.mark_failed(
                            response_id, message.code, message.message
                        )
                        yield encode_sse(
                            "response.failed", _failed_payload(response_id, message)
                        )
                    elif isinstance(message, WorkflowResultMessage):
                        result_markdown = message.result
                        updated = run_repository.save_revision(
                            response_id,
                            previous_revision=decision.revision,
                            spec_payload=markdown_spec_to_payload(result_markdown),
                            source="markdown_edit",
                            feedback=None,
                            edited_spec=None,
                        )
                        yield encode_sse(
                            "response.requires_confirmation",
                            _confirmation_payload(
                                response_id=response_id,
                                revision=updated.current_revision,
                                token=confirmation_token,
                                intent_payload=updated.intent_payload,
                                spec_markdown=markdown_spec_from_payload(
                                    updated.spec_payload
                                ),
                                expires_at=updated.expires_at,
                            ),
                        )

            return StreamingResponse(revision_stream(), media_type="text/event-stream")

        run_repository.record_confirmation(response_id, decision.revision)

        async def execution_stream() -> AsyncIterator[str]:
            def execute_workflow() -> FinalResponse:
                return execute_prepared_markdown_workflow(
                    prepared,
                    current_markdown,
                    event_logger,
                    runtime_options,
                    pipeline_factory,
                )

            async for message in _stream_operation(
                response_id=response_id,
                messages=messages,
                operation=execute_workflow,
                settings=settings,
                error_code="pipeline_execution_failed",
                safe_error="The data intelligence workflow could not complete.",
            ):
                if isinstance(message, str):
                    yield message
                elif isinstance(message, WorkflowFailedMessage):
                    run_repository.mark_failed(
                        response_id, message.code, message.message
                    )
                    yield encode_sse(
                        "response.failed", _failed_payload(response_id, message)
                    )
                elif isinstance(message, WorkflowResultMessage):
                    final_response = message.result
                    if not isinstance(final_response, FinalResponse):
                        failure = WorkflowFailedMessage(
                            code="pipeline_protocol_error",
                            message="The data intelligence workflow returned an invalid result.",
                        )
                        run_repository.mark_failed(
                            response_id, failure.code, failure.message
                        )
                        yield encode_sse(
                            "response.failed", _failed_payload(response_id, failure)
                        )
                        return
                    output_text = str(final_response.answer)
                    evidence = (
                        asdict(final_response.evidence)
                        if final_response.evidence is not None
                        else None
                    )
                    response_metadata = dict(final_response.metadata)
                    for delta in chunk_text(output_text):
                        yield encode_sse(
                            "response.output_text.delta",
                            {
                                "type": "response.output_text.delta",
                                "response_id": response_id,
                                "delta": delta,
                            },
                        )
                    yield encode_sse(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "response_id": response_id,
                            "text": output_text,
                        },
                    )
                    run_repository.mark_completed(
                        response_id,
                        output_text=output_text,
                        evidence=evidence,
                        response_metadata=response_metadata,
                    )
                    yield encode_sse(
                        "response.completed",
                        {
                            "type": "response.completed",
                            "response_id": response_id,
                            "response": {
                                "id": response_id,
                                "status": "completed",
                                "output_text": output_text,
                            },
                            "evidence": evidence,
                            "metadata": response_metadata,
                        },
                    )

        return StreamingResponse(execution_stream(), media_type="text/event-stream")

    return router
