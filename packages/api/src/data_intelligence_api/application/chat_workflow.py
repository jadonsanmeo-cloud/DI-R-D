from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from data_intelligence_api.domain.chat import ChatConversation

SYSTEM_PROMPT = (
    "You are a helpful data intelligence assistant. Answer clearly, keep useful "
    "context from the conversation, and ask a clarifying question when the user "
    "request is ambiguous."
)


class StreamingChatClient(Protocol):
    async def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        ...


class LLMChatWorkflow:
    def __init__(self, client: StreamingChatClient) -> None:
        self.client = client

    async def stream_response(
        self,
        *,
        conversation: ChatConversation,
        user_input: str,
        model_name: str,
    ) -> AsyncIterator[str]:
        del user_input
        messages = self._build_messages(conversation)
        async for delta in self.client.stream_chat(
            messages=messages,
            model=model_name or None,
        ):
            yield delta

    def _build_messages(
        self,
        conversation: ChatConversation,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        for item in conversation.messages:
            if not item.context:
                continue
            if item.role == "human":
                messages.append({"role": "user", "content": item.context})
            elif item.role in {"view", "ai"}:
                messages.append({"role": "assistant", "content": item.context})
            elif item.role == "system":
                messages.append({"role": "system", "content": item.context})
        return messages
