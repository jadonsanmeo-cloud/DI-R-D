import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from data_intelligence_api.application.chat_service import ChatService
from data_intelligence_api.domain.chat import ChatConversation
from data_intelligence_api.infrastructure.persistence.file_chat_store import (
    FileChatStore,
)


class FakeChatWorkflow:
    async def stream_response(
        self,
        *,
        conversation: ChatConversation,
        user_input: str,
        model_name: str,
    ) -> AsyncIterator[str]:
        del conversation, user_input, model_name
        yield "Hel"
        yield "lo"


class ChatServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_accumulated_answer_and_saves_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileChatStore(Path(temp_dir))
            conversation = store.create(
                chat_mode="chat_normal",
                model_name="cx/gpt-5.5",
            )
            store.save(conversation)
            service = ChatService(store=store, workflow=FakeChatWorkflow())

            chunks = [
                chunk
                async for chunk in service.complete_chat(
                    conv_uid=conversation.conv_uid,
                    chat_mode="chat_normal",
                    model_name="cx/gpt-5.5",
                    user_input="Hi",
                    user_name="user-1",
                    app_code="",
                )
            ]
            loaded = store.get(conversation.conv_uid)

        self.assertEqual(chunks, ["Hel", "Hello"])
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(
            [message.role for message in loaded.messages], ["human", "view"]
        )
        self.assertEqual(loaded.messages[0].context, "Hi")
        self.assertEqual(loaded.messages[1].context, "Hello")
        self.assertEqual(loaded.user_input, "Hi")


if __name__ == "__main__":
    unittest.main()
