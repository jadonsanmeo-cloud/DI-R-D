"""Stateless GenReport-backed Markdown report engine."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from data_intelligence_sdk.core.types import FinalResponse


class GenReportProtocolError(RuntimeError):
    pass


def _sse_payloads(chunks) -> list[dict[str, Any]]:
    buffer = ""
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        buffer += chunk.replace("\r\n", "\n")
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            payload = _sse_payload(raw_event)
            if payload is not None:
                events.append(payload)
    if buffer.strip():
        payload = _sse_payload(buffer)
        if payload is not None:
            events.append(payload)
    return events


def _sse_payload(raw_event: str) -> dict[str, Any] | None:
    data = "\n".join(
        line.removeprefix("data:").strip()
        for line in raw_event.splitlines()
        if line.startswith("data:")
    )
    if not data:
        return None
    payload = json.loads(data)
    return payload if isinstance(payload, dict) else None


async def _iter_sse_payloads(chunks: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    async for chunk in chunks:
        buffer += chunk.replace("\r\n", "\n")
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            payload = _sse_payload(raw_event)
            if payload is not None:
                yield payload
    if buffer.strip():
        payload = _sse_payload(buffer)
        if payload is not None:
            yield payload


def _absolute_url(value: object, public_base_url: str) -> object:
    if isinstance(value, list):
        return [_absolute_url(item, public_base_url) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"url", "proxy_url", "oss_url"} and isinstance(item, str):
            normalized[key] = (
                f"{public_base_url}{item}" if item.startswith("/") else item
            )
        else:
            normalized[key] = _absolute_url(item, public_base_url)
    return normalized


class GenReportMarkdownEngine:
    def __init__(
        self,
        base_url: str,
        *,
        operation_id: str = "",
        response_id: str = "",
        trace_id: str | None = None,
        model: str | None = None,
        language: str = "auto",
        history: list[dict[str, Any]] | None = None,
        public_base_url: str | None = None,
        execution_context: dict[str, Any] | None = None,
        execution_files: list[dict[str, Any]] | None = None,
        workspace_id: str | None = None,
        discover_workspace_files: bool = False,
        timeout_seconds: float = 900.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.public_base_url = (public_base_url or base_url).rstrip("/")
        self.operation_id = operation_id
        self.response_id = response_id
        self.trace_id = trace_id
        self.model = model
        self.language = language
        self.history = list(history or [])
        self.execution_context = execution_context
        self.execution_files = list(execution_files or [])
        self.workspace_id = workspace_id
        self.discover_workspace_files = discover_workspace_files
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def run_markdown(
        self,
        *,
        spec_markdown: str,
        organization_id: str,
        runtime: object,
        user_context: object,
        user_query: object,
    ) -> FinalResponse:
        del runtime, user_context, user_query
        payload = self.request_payload(
            instruction=spec_markdown,
            organization_id=organization_id,
        )
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/api/v1/reports:stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                response.raise_for_status()
                events = _sse_payloads(response.iter_text())

        output_chunks: list[str] = []
        process_events: list[dict[str, Any]] = []
        usage: dict[str, Any] | None = None
        completion: dict[str, Any] | None = None
        for event in events:
            self._validate_event_correlation(event)
            event_type = str(event.get("type") or "")
            event_payload = event.get("payload")
            event_payload = event_payload if isinstance(event_payload, dict) else {}
            if event_type == "report.output_text.delta":
                output_chunks.append(str(event_payload.get("delta") or ""))
            elif event_type == "report.usage":
                usage = dict(event_payload)
            elif event_type == "report.completed":
                completion = dict(event_payload)
            elif event_type == "report.failed":
                raise RuntimeError(
                    str(event_payload.get("message") or "GenReport execution failed.")
                )
            else:
                process_events.append(event)

        if completion is None:
            raise GenReportProtocolError("GenReport stream ended without completion")
        raw_artifacts = completion.get("artifacts")
        artifacts = [
            item for item in raw_artifacts or [] if isinstance(item, dict)
        ]
        normalized_artifacts = _absolute_url(artifacts, self.public_base_url)
        assert isinstance(normalized_artifacts, list)
        answer = str(completion.get("output_text") or "").strip()
        if not answer:
            answer = "".join(output_chunks).strip()
        if not answer:
            answer = "GenReport completed. Generated artifacts are available below."
        artifact_refs = [
            str(item.get("artifact_ref") or item.get("url") or item.get("filename"))
            for item in normalized_artifacts
            if isinstance(item, dict)
            and (item.get("artifact_ref") or item.get("url") or item.get("filename"))
        ]
        return FinalResponse(
            answer=answer,
            metadata={
                "route": "gen_report",
                "engine_name": "report",
                "generated_files": normalized_artifacts,
                "artifacts": artifact_refs,
                "usage": usage or completion.get("usage"),
                "process_events": process_events,
            },
        )

    async def stream_events(
        self,
        *,
        instruction: str,
        organization_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        payload = self.request_payload(
            instruction=instruction,
            organization_id=organization_id,
        )
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/v1/reports:stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                response.raise_for_status()
                async for event in _iter_sse_payloads(response.aiter_text()):
                    self._validate_event_correlation(event)
                    yield event

    def request_payload(
        self,
        *,
        instruction: str,
        organization_id: str,
    ) -> dict[str, Any]:
        if not self.operation_id or not self.response_id:
            raise GenReportProtocolError("GenReport operation correlation is required")
        if self.execution_context is None:
            raise GenReportProtocolError("GenReport requires an execution context")
        run_id = str(self.execution_context.get("run_id") or "")
        if not run_id:
            raise GenReportProtocolError("GenReport execution context has no run_id")
        return {
            "schema_version": "1",
            "operation_id": self.operation_id,
            "response_id": self.response_id,
            "run_id": run_id,
            "trace_id": self.trace_id,
            "instruction": instruction,
            "history": self.history,
            "model": self.model,
            "language": self.language,
            "organization_id": organization_id,
            "workspace_id": self.workspace_id,
            "execution_context": self.execution_context,
            "execution_files": self.execution_files,
            "runtime_gateway": self._runtime_gateway(),
            "discover_workspace_files": self.discover_workspace_files,
        }

    def _runtime_gateway(self) -> dict[str, Any]:
        context = self.execution_context
        if context is None:
            raise GenReportProtocolError("GenReport requires an execution context")
        endpoint = context.get("gateway_url")
        token = context.get("capability_token")
        if not isinstance(endpoint, str) or not isinstance(token, str):
            raise GenReportProtocolError("Runtime Gateway capability is incomplete")
        return {
            "run_id": context.get("run_id"),
            "endpoint": endpoint.rstrip("/"),
            "token": token,
            "token_type": "bearer",
            "expires_at": context.get("expires_at"),
            "workspace_id": self.workspace_id,
            "capabilities": list(context.get("capabilities") or []),
        }

    def _validate_event_correlation(self, event: dict[str, Any]) -> None:
        expected_run_id = (
            str(self.execution_context.get("run_id"))
            if self.execution_context is not None
            else ""
        )
        if (
            event.get("operation_id") != self.operation_id
            or event.get("response_id") != self.response_id
            or event.get("run_id") != expected_run_id
        ):
            raise GenReportProtocolError("GenReport event correlation mismatch")
