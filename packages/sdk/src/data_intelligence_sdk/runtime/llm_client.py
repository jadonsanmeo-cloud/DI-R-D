"""Small OpenAI-compatible chat completion client boundary."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol

from data_intelligence_sdk.runtime.tracing import traceable_llm_call


class LLMClient(Protocol):
    """Completion boundary used by SDK components."""

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Return a JSON object produced from chat messages."""

    def complete_text(self, messages: list[dict[str, str]]) -> str:
        """Return raw model text produced from chat messages."""


Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


class OpenAICompatibleLLMClient:
    """Calls an OpenAI-compatible `/chat/completions` endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0,
        timeout: int = 60,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or ""
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_COMPATIBLE_API_KEY") or ""
        self.model = model or os.environ.get("OPENAI_COMPATIBLE_MODEL") or ""
        self.temperature = temperature
        self.timeout = timeout
        self._transport = transport or self._default_transport

        if not self.base_url:
            raise ValueError("base_url is required for OpenAICompatibleLLMClient.")
        if not self.api_key:
            raise ValueError("api_key is required for OpenAICompatibleLLMClient.")
        if not self.model:
            raise ValueError("model is required for OpenAICompatibleLLMClient.")

        self._complete_json_traced = traceable_llm_call(
            self._complete_json_impl,
            name="openai_compatible.complete_json",
        )
        self._complete_text_traced = traceable_llm_call(
            self._complete_text_impl,
            name="openai_compatible.complete_text",
        )

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._complete_json_traced(messages)

    def complete_text(self, messages: list[dict[str, str]]) -> str:
        return self._complete_text_traced(messages)

    def _complete_json_impl(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = self._transport(
            f"{self.base_url}/chat/completions",
            headers,
            payload,
            self.timeout,
        )
        return self._extract_json_object(response)

    def _complete_text_impl(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = self._transport(
            f"{self.base_url}/chat/completions",
            headers,
            payload,
            self.timeout,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                "OpenAI-compatible response did not contain message content."
            ) from exc
        if not isinstance(content, str):
            raise ValueError("OpenAI-compatible message content must be text.")
        return content

    def _extract_json_object(self, response: dict[str, Any]) -> dict[str, Any]:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                "OpenAI-compatible response did not contain message content."
            ) from exc

        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            raise ValueError(
                "OpenAI-compatible message content must be a JSON object string."
            )

        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM response JSON must be an object.")
        return parsed

    def _default_transport(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise ConnectionError(f"OpenAI-compatible request failed: {exc}") from exc

        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI-compatible response must be a JSON object.")
        return parsed
