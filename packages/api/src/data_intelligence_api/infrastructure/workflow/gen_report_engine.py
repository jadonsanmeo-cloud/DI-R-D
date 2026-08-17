"""GenReport-backed Markdown report engine."""

from __future__ import annotations

import json
from typing import Any

import httpx

from data_intelligence_sdk.core.types import FinalResponse


def _sse_payloads(chunks) -> list[dict[str, Any]]:
    buffer = ""
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        buffer += chunk.replace("\r\n", "\n")
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            data = "\n".join(
                line.removeprefix("data:").strip()
                for line in raw_event.splitlines()
                if line.startswith("data:")
            )
            if not data:
                continue
            payload = json.loads(data)
            if isinstance(payload, dict):
                events.append(payload)
    return events


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
        del runtime, user_context
        query_text = str(getattr(user_query, "text", "") or "AXIOM report")
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            conversation_response = client.post(
                f"{self.base_url}/api/v1/conversations",
                json={"title": query_text, "model": None},
            )
            conversation_response.raise_for_status()
            conversation_payload = conversation_response.json()
            conversation_id = conversation_payload.get("hash_id") or conversation_payload.get(
                "id"
            )
            if conversation_id is None:
                raise RuntimeError("GenReport did not return a conversation ID.")

            payload: dict[str, Any] = {
                "message": spec_markdown,
                "conversation_id": str(conversation_id),
                "files": [],
                "analysis_mode": "auto",
                "language": "auto",
                "organization_id": organization_id,
                "workspace_id": self.workspace_id,
                "discover_workspace_files": self.discover_workspace_files,
                "execution_context": self.execution_context,
                "execution_files": self.execution_files,
            }
            runtime_gateway = self._runtime_gateway()
            if runtime_gateway is not None:
                payload["runtime_gateway"] = runtime_gateway

            with client.stream(
                "POST",
                f"{self.base_url}/api/v1/chat/stream",
                json=payload,
            ) as response:
                response.raise_for_status()
                events = _sse_payloads(response.iter_text())

        output_chunks: list[str] = []
        generated_files: list[dict[str, Any]] = []
        process_events: list[dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("type") or "")
            if event_type == "delta":
                output_chunks.append(str(event.get("content") or ""))
            elif event_type == "done":
                raw_files = event.get("generated_files")
                if isinstance(raw_files, list):
                    generated_files = [item for item in raw_files if isinstance(item, dict)]
            elif event_type == "error":
                raise RuntimeError(
                    str(event.get("content") or "GenReport report generation failed.")
                )
            else:
                process_events.append(event)

        normalized_files = _absolute_url(generated_files, self.public_base_url)
        assert isinstance(normalized_files, list)
        answer = "".join(output_chunks).strip() or (
            "GenReport completed. Generated files are available below."
        )
        return FinalResponse(
            answer=answer,
            metadata={
                "route": "gen_report",
                "engine_name": "report",
                "gen_report_conversation_id": str(conversation_id),
                "generated_files": normalized_files,
                "artifacts": [
                    str(item.get("url") or item.get("name"))
                    for item in normalized_files
                    if isinstance(item, dict) and (item.get("url") or item.get("name"))
                ],
                "process_events": process_events,
            },
        )

    def _runtime_gateway(self) -> dict[str, Any] | None:
        context = self.execution_context
        if context is None:
            return None
        endpoint = context.get("gateway_url")
        token = context.get("capability_token")
        if not isinstance(endpoint, str) or not isinstance(token, str):
            return None
        return {
            "run_id": context.get("run_id"),
            "endpoint": endpoint.rstrip("/"),
            "token": token,
            "token_type": "bearer",
            "expires_at": context.get("expires_at"),
        }
