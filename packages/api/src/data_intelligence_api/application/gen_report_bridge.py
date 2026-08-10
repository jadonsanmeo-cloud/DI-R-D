"""Responses API bridge for GenReport-backed report runs."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from data_intelligence_api.application.gen_report_client import GenReportClient
from data_intelligence_api.application.ports.run_repository import RunRepository
from data_intelligence_api.http.schemas.responses import CreateResponseRequest
from data_intelligence_api.http.streaming import encode_sse
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.persistence.run_store import (
    hash_confirmation_token,
)


class SseEventParser:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        self._buffer += chunk.replace("\r\n", "\n")
        events: list[dict[str, Any]] = []
        while "\n\n" in self._buffer:
            raw_event, self._buffer = self._buffer.split("\n\n", 1)
            data_lines = [
                line.removeprefix("data:").strip()
                for line in raw_event.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events


def _failed_payload(response_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "type": "response.failed",
        "response_id": response_id,
        "response": {"id": response_id, "status": "failed"},
        "error": {"code": code, "message": message},
    }


def _resolve_uploaded_files(
    payload: CreateResponseRequest,
    settings: ApiSettings,
) -> list[dict[str, Any]]:
    root = settings.data_corpus_root.resolve()
    upload_root = (root / ".uploads").resolve()
    files: list[dict[str, Any]] = []
    for item in payload.uploaded_files:
        filename = Path(item.filename.strip() or "upload").name
        relative_path = (
            item.relative_path.strip()
            if item.relative_path and item.relative_path.strip()
            else f".uploads/{filename}"
        )
        source_path = Path(relative_path)
        if source_path.is_absolute() or not relative_path.startswith(".uploads/"):
            raise HTTPException(status_code=400, detail="Invalid uploaded file path.")
        candidate = (root / source_path).resolve()
        try:
            candidate.relative_to(upload_root)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="Invalid uploaded file path."
            ) from error
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Uploaded file not found.")
        files.append(
            {
                "filename": filename,
                "relative_path": relative_path,
                "path": candidate,
                "content_type": item.content_type,
                "size": item.size,
            }
        )
    return files


def _lambda_file_metadata(
    files: list[dict[str, Any]],
    public_base_url: str,
) -> dict[str, Any]:
    artifacts = []
    for file_info in files:
        name = file_info.get("name") or file_info.get("filename") or ""
        url = file_info.get("url") or file_info.get("oss_url") or ""
        if isinstance(url, str) and url.startswith("/"):
            url = f"{public_base_url}{url}"
        if not name and not url:
            continue
        artifacts.append(
            {
                "name": name,
                "filename": name,
                "url": url,
                "type": file_info.get("type") or "file",
            }
        )
    return {
        "generated_files": artifacts,
        "artifacts": [
            item["url"] or item["name"]
            for item in artifacts
            if item.get("url") or item.get("name")
        ],
    }


def _lambda_tool_arguments(tool_call: dict[str, Any]) -> Any:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    arguments = function.get("arguments")
    if not isinstance(arguments, str) or not arguments.strip():
        return arguments
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def _absolutize_gen_report_urls(value: Any, public_base_url: str) -> Any:
    if isinstance(value, list):
        return [_absolutize_gen_report_urls(item, public_base_url) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"url", "proxy_url", "oss_url"} and isinstance(item, str):
            normalized[key] = (
                f"{public_base_url}{item}" if item.startswith("/") else item
            )
        else:
            normalized[key] = _absolutize_gen_report_urls(item, public_base_url)
    return normalized


def _lambda_process_event(
    *,
    response_id: str,
    lambda_event: dict[str, Any],
    sequence: int,
    public_base_url: str,
) -> dict[str, Any]:
    lambda_event = _absolutize_gen_report_urls(lambda_event, public_base_url)
    lambda_type = str(lambda_event.get("type") or "event")
    step = lambda_event.get("step")
    tool_call = lambda_event.get("tool_call")
    result = lambda_event.get("result")
    tool_call_id = (
        lambda_event.get("tool_call_id")
        if isinstance(lambda_event.get("tool_call_id"), str)
        else None
    )
    tool_name = (
        lambda_event.get("tool_name")
        if isinstance(lambda_event.get("tool_name"), str)
        else None
    )
    inputs: Any = None
    outputs: Any = None
    label = lambda_type

    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if isinstance(function, dict):
            tool_name = str(function.get("name") or tool_name or "tool")
        tool_call_id = str(tool_call.get("id") or tool_call_id or "")
        inputs = _lambda_tool_arguments(tool_call)
        label = tool_name or "tool call"
    elif lambda_type == "status":
        content = lambda_event.get("content")
        if isinstance(content, str) and content:
            label = content
    elif isinstance(tool_name, str) and tool_name:
        label = tool_name

    if result is not None:
        outputs = result

    event_id_parts = (
        ["tool", tool_call_id]
        if tool_call_id
        else [lambda_type, str(step) if step is not None else None, str(sequence)]
    )
    event_id = ":".join(part for part in event_id_parts if part)
    process_event = {
        "type": f"pipeline.lambda.{lambda_type}",
        "response_id": response_id,
        "event_id": event_id,
        "status": (
            "completed"
            if lambda_type in {"tool_result", "status", "keepalive"}
            else "running"
        ),
        "label": label,
        "details": lambda_event,
    }
    if inputs is not None:
        process_event["inputs"] = inputs
    if outputs is not None:
        process_event["outputs"] = outputs
    return process_event


async def stream_gen_report_response(
    *,
    payload: CreateResponseRequest,
    settings: ApiSettings,
    run_repository: RunRepository,
    response_id: str,
) -> AsyncIterator[str]:
    query_text = (payload.input or "").strip() or "Analyze this data corpus."
    client = GenReportClient(
        settings.gen_report_api_url,
        public_base_url=settings.gen_report_public_url,
    )
    request_payload = payload.model_dump(mode="json")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.spec_confirmation_ttl_seconds
    )
    run_repository.create_pending(
        response_id=response_id,
        token_hash=hash_confirmation_token(token),
        request_payload=request_payload,
        prepared_execution={},
        intent_payload={"value": "report"},
        spec_payload={"spec_markdown": query_text},
        user_id=payload.user_id,
        session_id=payload.session_id,
        expires_at=expires_at,
    )
    run_repository.record_confirmation(response_id, 1)

    process_events: list[dict[str, Any]] = []
    output_chunks: list[str] = []
    generated_files: list[dict[str, Any]] = []

    yield encode_sse(
        "response.created",
        {
            "type": "response.created",
            "response_id": response_id,
            "response": {"id": response_id, "status": "in_progress"},
        },
    )

    try:
        uploaded_files = _resolve_uploaded_files(payload, settings)
        gen_conversation_id = await client.create_conversation(query_text)
        conversation_event = {
            "type": "pipeline.lambda.conversation_created",
            "response_id": response_id,
            "status": "completed",
            "label": "GenReport conversation created",
            "details": {"outputs": {"conversation_id": gen_conversation_id}},
        }
        process_events.append(conversation_event)
        yield encode_sse("pipeline.lambda.conversation_created", conversation_event)

        file_ids: list[int] = []
        for uploaded in uploaded_files:
            file_id = await client.upload_file(
                conversation_id=gen_conversation_id,
                filename=str(uploaded["filename"]),
                path=uploaded["path"],
                content_type=uploaded["content_type"],
            )
            file_ids.append(file_id)
            uploaded_event = {
                "type": "pipeline.lambda.file_uploaded",
                "response_id": response_id,
                "status": "completed",
                "label": "Uploaded file to GenReport",
                "details": {
                    "inputs": {
                        "filename": uploaded["filename"],
                        "relative_path": uploaded["relative_path"],
                    },
                    "outputs": {"gen_report_file_id": file_id},
                },
            }
            process_events.append(uploaded_event)
            yield encode_sse("pipeline.lambda.file_uploaded", uploaded_event)

        parser = SseEventParser()
        lambda_event_sequence = 0
        async for chunk in client.stream_chat(
            conversation_id=gen_conversation_id,
            message=query_text,
            file_ids=file_ids,
        ):
            for lambda_event in parser.feed(chunk):
                lambda_type = str(lambda_event.get("type") or "")
                if lambda_type == "delta":
                    delta = str(lambda_event.get("content") or "")
                    if delta:
                        output_chunks.append(delta)
                        yield encode_sse(
                            "response.output_text.delta",
                            {
                                "type": "response.output_text.delta",
                                "response_id": response_id,
                                "delta": delta,
                            },
                        )
                    continue
                if lambda_type == "done":
                    raw_files = lambda_event.get("generated_files")
                    if isinstance(raw_files, list):
                        generated_files = [
                            item for item in raw_files if isinstance(item, dict)
                        ]
                        if generated_files:
                            lambda_event_sequence += 1
                            files_event = {
                                "type": "pipeline.lambda.workspace_files",
                                "response_id": response_id,
                                "event_id": f"workspace_files:{lambda_event_sequence}",
                                "status": "completed",
                                "label": "Workspace files updated",
                                "details": {"generated_files": generated_files},
                                "outputs": {"generated_files": generated_files},
                            }
                            files_event = _absolutize_gen_report_urls(
                                files_event,
                                client.public_base_url,
                            )
                            process_events.append(files_event)
                            yield encode_sse(
                                "pipeline.lambda.workspace_files",
                                files_event,
                            )
                    continue
                if lambda_type == "error":
                    raise RuntimeError(
                        str(
                            lambda_event.get("content")
                            or "GenReport report generation failed."
                        )
                    )

                lambda_event_sequence += 1
                process_event = _lambda_process_event(
                    response_id=response_id,
                    lambda_event=lambda_event,
                    sequence=lambda_event_sequence,
                    public_base_url=client.public_base_url,
                )
                process_events.append(process_event)
                yield encode_sse(process_event["type"], process_event)

        output_text = "".join(output_chunks).strip() or (
            "GenReport completed. Generated files are available below."
        )
        metadata = {
            "route": "gen_report",
            "engine_name": "report",
            "title": "GenReport report",
            "summary": output_text[:240],
            "gen_report_conversation_id": gen_conversation_id,
            "process_events": process_events,
            **_lambda_file_metadata(generated_files, client.public_base_url),
        }
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
            evidence=None,
            response_metadata=metadata,
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
                "metadata": metadata,
            },
        )
    except Exception as error:
        message = str(error) or "GenReport report generation failed."
        run_repository.mark_failed(response_id, "gen_report_failed", message)
        yield encode_sse(
            "response.failed",
            _failed_payload(response_id, "gen_report_failed", message),
        )
