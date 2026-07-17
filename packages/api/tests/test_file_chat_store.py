import tempfile
import unittest
from pathlib import Path

from data_intelligence_api.domain.chat import ChatMessage
from data_intelligence_api.infrastructure.persistence.file_chat_store import (
    FileChatStore,
)


class FileChatStoreTests(unittest.TestCase):
    def test_create_save_load_and_list_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileChatStore(Path(temp_dir))

            conversation = store.create(
                chat_mode="chat_normal",
                model_name="cx/gpt-5.5",
                user_name="user-1",
            )
            conversation.user_input = "Hello"
            conversation.messages.append(
                ChatMessage(
                    role="human",
                    context="Hello",
                    order=1,
                    time_stamp=1000,
                    model_name="cx/gpt-5.5",
                )
            )
            store.save(conversation)

            loaded = store.get(conversation.conv_uid)
            listed = store.list()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.conv_uid, conversation.conv_uid)
        self.assertEqual(loaded.user_input, "Hello")
        self.assertEqual(loaded.messages[0].role, "human")
        self.assertEqual(loaded.messages[0].context, "Hello")
        self.assertEqual([item.conv_uid for item in listed], [conversation.conv_uid])

    def test_rejects_unsafe_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileChatStore(Path(temp_dir))

            with self.assertRaisesRegex(ValueError, "Invalid conversation id"):
                store.get("../secret")


if __name__ == "__main__":
    unittest.main()
