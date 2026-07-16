import unittest
from collections.abc import AsyncIterator

from data_intelligence_api.application.chat_workflow import LLMChatWorkflow
from data_intelligence_api.domain.chat import ChatConversation, ChatMessage


class RecordingChatClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None
        self.model: str | None = None

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        del temperature, max_tokens
        self.messages = messages
        self.model = model
        yield "ok"


class ChatWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_llm_messages_from_conversation_history(self) -> None:
        client = RecordingChatClient()
        workflow = LLMChatWorkflow(client)
        conversation = ChatConversation(conv_uid="conv_1")
        conversation.messages.extend(
            [
                ChatMessage(
                    role="human",
                    context="Hello",
                    order=1,
                    time_stamp=1000,
                    model_name="cx/gpt-5.5",
                ),
                ChatMessage(
                    role="view",
                    context="Hi there",
                    order=1,
                    time_stamp=1001,
                    model_name="cx/gpt-5.5",
                ),
            ]
        )

        chunks = [
            chunk
            async for chunk in workflow.stream_response(
                conversation=conversation,
                user_input="Next",
                model_name="cx/gpt-5.5",
            )
        ]

        self.assertEqual(chunks, ["ok"])
        self.assertEqual(client.model, "cx/gpt-5.5")
        self.assertIsNotNone(client.messages)
        assert client.messages is not None
        self.assertEqual(client.messages[0]["role"], "system")
        self.assertEqual(client.messages[1], {"role": "user", "content": "Hello"})
        self.assertEqual(
            client.messages[2],
            {"role": "assistant", "content": "Hi there"},
        )


if __name__ == "__main__":
    unittest.main()
