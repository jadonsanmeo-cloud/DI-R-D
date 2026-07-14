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

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import ApiSettings
from backend.run_store import (
    RunConflictError,
    RunExpiredError,
    RunNotFoundError,
    RunStore,
    hash_confirmation_token,
)
from backend.schemas import CreateResponseRequest, ResponseDecisionRequest
from backend.streaming import (
    PipelineLogMessage,
    QueueRuntimeLogger,
    WorkflowFailedMessage,
    WorkflowResultMessage,
    chunk_text,
    encode_sse,
)
from backend.workflow import (
    PipelineFactory,
    SourceValidationError,
    build_workflow_invocation,
    default_pipeline_factory,
    execute_prepared_workflow,
    prepare_workflow,
    prepared_from_payload,
    prepared_to_payload,
    revise_workflow,
    spec_from_payload,
    spec_to_payload,
)
from data_intelligence_sdk.core.types import ExecutionSpec, FinalResponse


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
    spec_payload: dict,
    expires_at: datetime,
) -> dict:
    return {
        "type": "response.requires_confirmation",
        "response_id": response_id,
        "revision": revision,
        "confirmation_token": token,
        "intent": intent_payload,
        "spec": spec_payload,
        "expires_at": expires_at.isoformat(),
    }


def _raise_store_error(error: Exception) -> None:
    if isinstance(error, RunNotFoundError):
        raise HTTPException(status_code=404, detail="Response was not found.") from error
    if isinstance(error, RunExpiredError):
        raise HTTPException(status_code=410, detail=str(error)) from error
    if isinstance(error, RunConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise error


def _edited_spec(payload: dict, intent: str) -> ExecutionSpec:
    return spec_from_payload(
        {
            "intent": intent,
            **payload,
            "confirmed": False,
        }
    )


def create_responses_router(
    settings: ApiSettings,
    pipeline_factory: PipelineFactory = default_pipeline_factory,
    run_store: RunStore | None = None,
) -> APIRouter:
    if run_store is None:
        raise ValueError("run_store is required for resumable Responses workflows.")
    router = APIRouter()

    @router.post("/api/v1/responses")
    async def create_response(payload: CreateResponseRequest) -> StreamingResponse:
        try:
            invocation = build_workflow_invocation(payload, settings.data_corpus_root)
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
                    spec_payload = spec_to_payload(prepared.spec)
                    intent_payload = {"value": prepared.intent}
                    run_store.create_pending(
                        response_id=response_id,
                        token_hash=hash_confirmation_token(confirmation_token),
                        request_payload=payload.model_dump(mode="json"),
                        prepared_execution=prepared_to_payload(prepared),
                        intent_payload=intent_payload,
                        spec_payload=spec_payload,
                        user_id=payload.user_id,
                        session_id=payload.session_id,
                        expires_at=expires_at,
                    )
                    confirmation = _confirmation_payload(
                        response_id=response_id,
                        revision=1,
                        token=confirmation_token,
                        intent_payload=intent_payload,
                        spec_payload=spec_payload,
                        expires_at=expires_at,
                    )
                    yield encode_sse("response.requires_confirmation", confirmation)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.get("/api/v1/responses/{response_id}")
    async def get_response(
        response_id: str,
        confirmation_token: str = Header(alias="X-Confirmation-Token"),
    ) -> JSONResponse:
        try:
            run = run_store.get_authorized(response_id, confirmation_token)
        except Exception as error:
            _raise_store_error(error)
        return JSONResponse(
            {
                "response_id": run.response_id,
                "status": run.status,
                "revision": run.current_revision,
                "intent": run.intent_payload,
                "spec": run.spec_payload,
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
            raise HTTPException(status_code=409, detail="Maximum spec revisions reached.")
        target_status = "revising" if decision.action == "revise" else "executing"
        try:
            run = run_store.claim(
                response_id,
                confirmation_token,
                revision=decision.revision,
                target_status=target_status,
            )
        except Exception as error:
            _raise_store_error(error)

        current_spec = spec_from_payload(run.spec_payload)
        prepared = prepared_from_payload(run.prepared_execution, current_spec)
        messages: queue.Queue[object] = queue.Queue()
        event_logger = QueueRuntimeLogger(messages)

        if decision.action == "revise":
            edited_payload = (
                decision.edited_spec.model_dump(mode="json")
                if decision.edited_spec is not None
                else None
            )
            base_spec = (
                _edited_spec(edited_payload, current_spec.intent)
                if edited_payload is not None
                else current_spec
            )
            feedback = (decision.feedback or "").strip()

            async def revision_stream() -> AsyncIterator[str]:
                operation = (
                    lambda: revise_workflow(
                        prepared,
                        base_spec,
                        feedback,
                        event_logger,
                        pipeline_factory,
                    )
                    if feedback
                    else lambda: base_spec
                )
                if not feedback:
                    operation = lambda: base_spec
                    event_logger.log(
                        "pipeline.spec_revision_started", {"structured_edit": True}
                    )
                    event_logger.log(
                        "pipeline.spec_revised", {"objective": base_spec.objective}
                    )
                async for message in _stream_operation(
                    response_id=response_id,
                    messages=messages,
                    operation=operation,
                    settings=settings,
                    error_code="spec_revision_failed",
                    safe_error="The execution spec could not be revised.",
                ):
                    if isinstance(message, str):
                        yield message
                    elif isinstance(message, WorkflowFailedMessage):
                        run_store.mark_failed(response_id, message.code, message.message)
                        yield encode_sse(
                            "response.failed", _failed_payload(response_id, message)
                        )
                    elif isinstance(message, WorkflowResultMessage):
                        revised_spec = message.result
                        revised_spec.confirmed = False
                        source = (
                            "structured_edit_and_feedback"
                            if edited_payload is not None and feedback
                            else "structured_edit"
                            if edited_payload is not None
                            else "feedback_revision"
                        )
                        updated = run_store.save_revision(
                            response_id,
                            previous_revision=decision.revision,
                            spec_payload=spec_to_payload(revised_spec),
                            source=source,
                            feedback=feedback or None,
                            edited_spec=edited_payload,
                        )
                        yield encode_sse(
                            "response.requires_confirmation",
                            _confirmation_payload(
                                response_id=response_id,
                                revision=updated.current_revision,
                                token=confirmation_token,
                                intent_payload=updated.intent_payload,
                                spec_payload=updated.spec_payload,
                                expires_at=updated.expires_at,
                            ),
                        )

            return StreamingResponse(revision_stream(), media_type="text/event-stream")

        current_spec.confirmed = True
        run_store.record_confirmation(response_id, decision.revision)

        async def execution_stream() -> AsyncIterator[str]:
            async for message in _stream_operation(
                response_id=response_id,
                messages=messages,
                operation=lambda: execute_prepared_workflow(
                    prepared, current_spec, event_logger, pipeline_factory
                ),
                settings=settings,
                error_code="pipeline_execution_failed",
                safe_error="The data intelligence workflow could not complete.",
            ):
                if isinstance(message, str):
                    yield message
                elif isinstance(message, WorkflowFailedMessage):
                    run_store.mark_failed(response_id, message.code, message.message)
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
                        run_store.mark_failed(
                            response_id, failure.code, failure.message
                        )
                        yield encode_sse(
                            "response.failed", _failed_payload(response_id, failure)
                        )
                        return
                    output_text = str(final_response.answer)
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
                    run_store.mark_completed(response_id)
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
                            "evidence": asdict(final_response.evidence)
                            if final_response.evidence is not None
                            else None,
                            "metadata": dict(final_response.metadata),
                        },
                    )

        return StreamingResponse(execution_stream(), media_type="text/event-stream")

    return router
