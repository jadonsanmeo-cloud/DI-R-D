import json
import queue
import unittest

from backend.streaming import (
    PipelineLogMessage,
    QueueRuntimeLogger,
    chunk_text,
    encode_sse,
)


class BackendStreamingTests(unittest.TestCase):
    def test_runtime_logger_places_lifecycle_message_on_queue(self) -> None:
        messages: queue.Queue[object] = queue.Queue()
        logger = QueueRuntimeLogger(messages)

        logger.log("pipeline.start", {"query": "answer"})

        message = messages.get_nowait()
        self.assertEqual(
            message,
            PipelineLogMessage(
                event="pipeline.start",
                payload={"query": "answer"},
            ),
        )

    def test_encode_sse_sets_event_and_single_line_json_data(self) -> None:
        encoded = encode_sse(
            "response.created",
            {"type": "response.created", "response_id": "resp_1"},
        )

        self.assertTrue(encoded.startswith("event: response.created\n"))
        data_line = encoded.splitlines()[1]
        self.assertTrue(data_line.startswith("data: "))
        self.assertEqual(
            json.loads(data_line.removeprefix("data: ")),
            {"type": "response.created", "response_id": "resp_1"},
        )
        self.assertTrue(encoded.endswith("\n\n"))

    def test_chunk_text_preserves_content(self) -> None:
        chunks = list(chunk_text("abcdefgh", chunk_size=3))

        self.assertEqual(chunks, ["abc", "def", "gh"])
        self.assertEqual("".join(chunks), "abcdefgh")

    def test_empty_text_has_no_delta_chunks(self) -> None:
        self.assertEqual(list(chunk_text("", chunk_size=3)), [])


if __name__ == "__main__":
    unittest.main()
