from __future__ import annotations

import asyncio
import unittest

from data_intelligence_api.application.query_orchestrator import (
    DelegateToDataFlow,
    DirectGeneralAnswer,
    GeneralQueryOrchestrator,
    OrchestratorModelResponse,
)
from data_intelligence_sdk.core.types import SessionContext, UserQuery
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    async def decide(self, *, messages, tool_name):
        self.calls.append((messages, tool_name))
        if self.error is not None:
            raise self.error
        return self.response


class GeneralQueryOrchestratorTests(unittest.TestCase):
    def test_returns_direct_general_answer_from_model_text(self) -> None:
        client = FakeClient(OrchestratorModelResponse(text="PostgreSQL is a database."))
        logger = InMemoryRuntimeLogger()
        orchestrator = GeneralQueryOrchestrator(client, logger=logger)

        result = asyncio.run(
            orchestrator.route(
                UserQuery(text="What is PostgreSQL?"),
                SessionContext(turns=[{"role": "user", "text": "I am learning SQL"}]),
            )
        )

        self.assertEqual(result, DirectGeneralAnswer("PostgreSQL is a database."))
        self.assertEqual(client.calls[0][1], "delegate_to_data_flow")
        self.assertIn("I am learning SQL", str(client.calls[0][0]))
        self.assertIn("What is PostgreSQL?", str(client.calls[0][0]))
        self.assertIn("orchestrator.direct_answer.selected", [event for event, _ in logger.events])

    def test_delegates_when_model_calls_data_flow_tool(self) -> None:
        orchestrator = GeneralQueryOrchestrator(
            FakeClient(
                OrchestratorModelResponse(
                    text=None,
                    tool_calls=("delegate_to_data_flow",),
                )
            )
        )

        result = asyncio.run(
            orchestrator.route(UserQuery(text="Summarize my latest reports"))
        )

        self.assertEqual(result, DelegateToDataFlow())

    def test_tool_call_wins_over_partial_assistant_text(self) -> None:
        orchestrator = GeneralQueryOrchestrator(
            FakeClient(
                OrchestratorModelResponse(
                    text="I will inspect your reports...",
                    tool_calls=("delegate_to_data_flow",),
                )
            )
        )

        result = asyncio.run(orchestrator.route(UserQuery(text="Compare my reports")))

        self.assertEqual(result, DelegateToDataFlow())

    def test_unknown_tool_call_fails_closed_to_delegation(self) -> None:
        orchestrator = GeneralQueryOrchestrator(
            FakeClient(
                OrchestratorModelResponse(text=None, tool_calls=("unknown_tool",))
            )
        )

        result = asyncio.run(orchestrator.route(UserQuery(text="Use my data")))

        self.assertEqual(result, DelegateToDataFlow())

    def test_empty_direct_answer_is_rejected(self) -> None:
        orchestrator = GeneralQueryOrchestrator(
            FakeClient(OrchestratorModelResponse(text="   "))
        )

        with self.assertRaisesRegex(RuntimeError, "empty answer"):
            asyncio.run(orchestrator.route(UserQuery(text="Hello")))

    def test_provider_error_is_not_silently_delegated(self) -> None:
        orchestrator = GeneralQueryOrchestrator(
            FakeClient(error=ConnectionError("provider unavailable"))
        )

        with self.assertRaisesRegex(ConnectionError, "provider unavailable"):
            asyncio.run(orchestrator.route(UserQuery(text="Hello")))


if __name__ == "__main__":
    unittest.main()
