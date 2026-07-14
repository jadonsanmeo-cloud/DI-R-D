import unittest

from data_intelligence_sdk.runtime.llm_client import OpenAICompatibleLLMClient


class OpenAICompatibleLLMClientTests(unittest.TestCase):
    def test_complete_json_posts_openai_compatible_chat_request(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"objective": "Analyze revenue"}',
                        }
                    }
                ]
            }

        client = OpenAICompatibleLLMClient(
            base_url="http://localhost:20128/v1",
            api_key="test-key",
            model="cx/gpt-5.5",
            transport=transport,
            timeout=12,
        )

        result = client.complete_json(
            [{"role": "user", "content": "Build an execution spec"}]
        )

        self.assertEqual(result, {"objective": "Analyze revenue"})
        self.assertEqual(
            captured["url"], "http://localhost:20128/v1/chat/completions"
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["payload"]["model"], "cx/gpt-5.5")
        self.assertEqual(captured["payload"]["temperature"], 0)
        self.assertEqual(
            captured["payload"]["response_format"], {"type": "json_object"}
        )
        self.assertEqual(captured["timeout"], 12)

    def test_constructor_requires_api_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            OpenAICompatibleLLMClient(base_url="", api_key="key", model="model")
        with self.assertRaisesRegex(ValueError, "api_key"):
            OpenAICompatibleLLMClient(base_url="http://localhost:20128/v1", api_key="", model="model")
        with self.assertRaisesRegex(ValueError, "model"):
            OpenAICompatibleLLMClient(base_url="http://localhost:20128/v1", api_key="key", model="")


if __name__ == "__main__":
    unittest.main()
