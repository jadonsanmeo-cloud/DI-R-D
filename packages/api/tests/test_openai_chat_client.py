import unittest

from data_intelligence_api.infrastructure.llm.openai_chat_client import OpenAIChatClient


class OpenAIChatClientTests(unittest.TestCase):
    def test_parse_openai_delta_sse_line(self) -> None:
        line = 'data: {"choices":[{"delta":{"content":"Hel"}}]}'

        result = OpenAIChatClient._parse_sse_line(line)

        self.assertEqual(result, "Hel")

    def test_parse_message_content_sse_line(self) -> None:
        line = 'data: {"choices":[{"message":{"content":"Hello"}}]}'

        result = OpenAIChatClient._parse_sse_line(line)

        self.assertEqual(result, "Hello")

    def test_ignores_done_line(self) -> None:
        result = OpenAIChatClient._parse_sse_line("data: [DONE]")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
