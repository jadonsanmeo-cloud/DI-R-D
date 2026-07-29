from __future__ import annotations

import asyncio
import unittest

from data_intelligence_api.infrastructure.llm.query_orchestrator_client import (
    OpenAIQueryOrchestratorClient,
)


class RecordingTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = []

    async def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return self.response


class OpenAIQueryOrchestratorClientTests(unittest.TestCase):
    def client(self, response: dict):
        transport = RecordingTransport(response)
        return (
            OpenAIQueryOrchestratorClient(
                base_url="https://provider.example/v1",
                api_key="secret",
                model="router-model",
                transport=transport,
            ),
            transport,
        )

    def test_request_exposes_only_the_delegation_tool(self) -> None:
        client, transport = self.client(
            {"choices": [{"message": {"content": "A direct answer."}}]}
        )

        result = asyncio.run(
            client.decide(
                messages=[{"role": "user", "content": "What is SQL?"}],
                tool_name="delegate_to_data_flow",
            )
        )

        payload = transport.calls[0][2]
        self.assertEqual(result.text, "A direct answer.")
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(len(payload["tools"]), 1)
        self.assertEqual(
            payload["tools"][0]["function"]["name"],
            "delegate_to_data_flow",
        )
        self.assertEqual(payload["tools"][0]["function"]["parameters"], {"type": "object", "properties": {}, "additionalProperties": False})

    def test_parses_delegation_tool_call_and_partial_text(self) -> None:
        client, _ = self.client(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Let me inspect that.",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "delegate_to_data_flow",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        result = asyncio.run(
            client.decide(messages=[], tool_name="delegate_to_data_flow")
        )

        self.assertEqual(result.text, "Let me inspect that.")
        self.assertEqual(result.tool_calls, ("delegate_to_data_flow",))

    def test_malformed_tool_call_is_preserved_as_a_safe_delegation_signal(self) -> None:
        client, _ = self.client(
            {"choices": [{"message": {"content": None, "tool_calls": [{}]}}]}
        )

        result = asyncio.run(
            client.decide(messages=[], tool_name="delegate_to_data_flow")
        )

        self.assertEqual(result.tool_calls, ("__malformed_tool_call__",))

    def test_missing_message_is_rejected(self) -> None:
        client, _ = self.client({"choices": []})

        with self.assertRaisesRegex(RuntimeError, "message"):
            asyncio.run(client.decide(messages=[], tool_name="delegate_to_data_flow"))


if __name__ == "__main__":
    unittest.main()
