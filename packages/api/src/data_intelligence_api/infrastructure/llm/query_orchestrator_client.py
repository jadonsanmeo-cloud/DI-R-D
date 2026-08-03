"""OpenAI-compatible native tool-calling client for query routing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from data_intelligence_api.application.query_orchestrator import (
    OrchestratorModelResponse,
    OrchestratorModelStreamChunk,
)


Transport = Callable[
    [str, dict[str, str], dict[str, Any], float],
    Awaitable[dict[str, Any]],
]
StreamTransport = Callable[
    [str, dict[str, str], dict[str, Any], float],
    AsyncIterator[dict[str, Any]],
]


class OpenAIQueryOrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        transport: Transport | None = None,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required.")
        if not model.strip():
            raise ValueError("model is required.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _httpx_transport
        self.stream_transport = stream_transport or _httpx_stream_transport

    async def decide(
        self,
        *,
        messages: list[dict[str, str]],
        tool_name: str,
    ) -> OrchestratorModelResponse:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter API key is required for query orchestration."
            )
        payload = self._request_payload(messages=messages, tool_name=tool_name)
        response = await self.transport(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout_seconds,
        )
        message = _first_message(response)
        content = message.get("content")
        text = content if isinstance(content, str) else None
        return OrchestratorModelResponse(
            text=text,
            tool_calls=_tool_call_names(message.get("tool_calls")),
        )

    async def decide_stream(
        self,
        *,
        messages: list[dict[str, str]],
        tool_name: str,
    ) -> AsyncIterator[OrchestratorModelStreamChunk]:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter API key is required for query orchestration."
            )
        payload = self._request_payload(messages=messages, tool_name=tool_name)
        payload["stream"] = True
        async for event in self.stream_transport(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout_seconds,
        ):
            delta = _first_delta(event)
            tool_calls = _tool_call_names(delta.get("tool_calls"))
            if tool_calls:
                yield OrchestratorModelStreamChunk(tool_calls=tool_calls)
                return
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield OrchestratorModelStreamChunk(text_delta=content)

    def _request_payload(
        self,
        *,
        messages: list[dict[str, str]],
        tool_name: str,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": (
                            "Delegate the unchanged request to the private-data "
                            "intent and report workflow."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "temperature": 0,
        }


def _first_message(response: dict[str, Any]) -> dict[str, Any]:
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Orchestrator response did not contain a message.") from exc
    if not isinstance(message, dict):
        raise RuntimeError("Orchestrator response message must be an object.")
    return message

def _first_delta(response: dict[str, Any]) -> dict[str, Any]:
    try:
        delta = response["choices"][0]["delta"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Orchestrator stream chunk did not contain a delta.") from exc
    if not isinstance(delta, dict):
        raise RuntimeError("Orchestrator stream delta must be an object.")
    return delta


def _tool_call_names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return ("__malformed_tool_call__",)
    names: list[str] = []
    for call in value:
        if not isinstance(call, dict):
            names.append("__malformed_tool_call__")
            continue
        function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        names.append(name if isinstance(name, str) and name else "__malformed_tool_call__")
    return tuple(names)


async def _httpx_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Orchestrator response must be a JSON object.")
    return data

async def _httpx_stream_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> AsyncIterator[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                parsed = json.loads(data)
                if not isinstance(parsed, dict):
                    raise RuntimeError(
                        "Orchestrator stream chunk must be a JSON object."
                    )
                yield parsed
