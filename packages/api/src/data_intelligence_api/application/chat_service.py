from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from data_intelligence_api.domain.chat import ChatConversation, ChatMessage
from data_intelligence_api.infrastructure.persistence.file_chat_store import (
    FileChatStore,
    current_millis,
)


class ChatWorkflow(Protocol):
    async def stream_response(
        self,
        *,
        conversation: ChatConversation,
        user_input: str,
        model_name: str,
    ) -> AsyncIterator[str]:
        ...


class ChatService:
    def __init__(self, *, store: FileChatStore, workflow: ChatWorkflow) -> None:
        self.store = store
        self.workflow = workflow

    def create_conversation(
        self,
        *,
        chat_mode: str,
        model_name: str,
        user_name: str = "default",
        app_code: str = "",
    ) -> ChatConversation:
        conversation = self.store.create(
            chat_mode=chat_mode,
            model_name=model_name,
            user_name=user_name,
            app_code=app_code,
        )
        self.store.save(conversation)
        return conversation

    def list_conversations(self) -> list[ChatConversation]:
        return self.store.list()

    def get_history(self, conv_uid: str) -> list[ChatMessage]:
        conversation = self.store.get(conv_uid)
        if conversation is None:
            return []
        return conversation.messages

    def delete_conversation(self, conv_uid: str) -> bool:
        return self.store.delete(conv_uid)

    def clear_conversation(self, conv_uid: str) -> ChatConversation | None:
        return self.store.clear(conv_uid)

    async def complete_chat(
        self,
        *,
        conv_uid: str,
        chat_mode: str,
        model_name: str,
        user_input: str,
        user_name: str = "default",
        app_code: str = "",
    ) -> AsyncIterator[str]:
        conversation = self.store.get(conv_uid)

        if conversation is None:
            conversation = self.store.create(
                chat_mode=chat_mode,
                model_name=model_name,
                user_name=user_name,
                app_code=app_code,
            )

        conversation.chat_mode = chat_mode or conversation.chat_mode
        conversation.model_name = model_name or conversation.model_name
        conversation.user_name = user_name or conversation.user_name
        conversation.app_code = app_code or conversation.app_code

        order = self._next_order(conversation)

        conversation.messages.append(
            ChatMessage(
                role="human",
                context=user_input,
                order=order,
                time_stamp=current_millis(),
                model_name=model_name,
            )
        )

        if not conversation.user_input:
            conversation.user_input = user_input

        accumulated_answer = ""

        try:
            async for delta in self.workflow.stream_response(
                conversation=conversation,
                user_input=user_input,
                model_name=model_name,
            ):
                accumulated_answer += delta
                yield accumulated_answer
        finally:
            if accumulated_answer:
                conversation.messages.append(
                    ChatMessage(
                        role="view",
                        context=accumulated_answer,
                        order=order,
                        time_stamp=current_millis(),
                        model_name=model_name,
                    )
                )
                self.store.save(conversation)

    @staticmethod
    def _next_order(conversation: ChatConversation) -> int:
        if not conversation.messages:
            return 1
        return max(message.order for message in conversation.messages) + 1