from __future__ import annotations

import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger
from data_intelligence_sdk.spec.llm_builder import LLMSpecBuilder


class SequencedClient:
    def __init__(self) -> None:
        self.payloads = [
            {"objective": ""},
            {
                "objective": "Inspect orders",
                "data_requirements": ["orders.csv"],
                "capability_requirements": [{"name": "inspect_data"}],
                "constraints": {},
                "engine_hint": None,
            },
        ]

    def complete_json(self, messages: object) -> dict[str, object]:
        return self.payloads.pop(0)


class LlmSpecLoggingTests(unittest.TestCase):
    def test_logs_context_attempt_retry_and_completion_without_raw_payload(self) -> None:
        logger = InMemoryRuntimeLogger()
        builder = LLMSpecBuilder(
            SequencedClient(),
            logger=logger,
            max_validation_retries=1,
        )

        builder.build(
            UserQuery(text="Inspect orders"),
            "reason",
            DataCorpusPackage(sources=["orders.csv"]),
        )

        events = [event for event, payload in logger.events]
        self.assertEqual(
            events,
            [
                "spec.context_built",
                "spec.llm_attempt.started",
                "spec.validation_retry",
                "spec.llm_attempt.started",
                "spec.llm_attempt.completed",
            ],
        )
        retry_payload = logger.events[2][1]
        self.assertEqual(retry_payload["error_type"], "ValueError")
        self.assertNotIn("payload", retry_payload)


if __name__ == "__main__":
    unittest.main()
