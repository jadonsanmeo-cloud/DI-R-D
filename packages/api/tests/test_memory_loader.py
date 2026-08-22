import json
import unittest

import httpx

from data_intelligence_api.infrastructure.memory import (
    AxiomExperienceMemoryLoader,
    DisabledMemoryLoader,
)
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger


class AxiomExperienceMemoryLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_loader_skips_without_network(self) -> None:
        logger = InMemoryRuntimeLogger()

        context = await DisabledMemoryLoader(logger=logger).load(
            query="Any query",
            tenant_id="test-org",
            user_id="user-1",
            workspace_id=None,
            agent_id=None,
            session_id=None,
            trace_id=None,
        )

        self.assertFalse(context.loaded)
        self.assertEqual(context.mode, "disabled")
        self.assertEqual(
            logger.events,
            [("memory.load.skipped", {"reason": "disabled"})],
        )

    async def test_load_sends_scoped_hybrid_search_and_parses_memory_cards(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "memories": [
                        {
                            "memory_id": "11111111-1111-1111-1111-111111111111",
                            "memory_type": "preference",
                            "content": "Prefer concise Markdown.",
                            "confidence": 0.9,
                            "importance": 0.8,
                            "scope": {
                                "tenant_id": "test-org",
                                "user_id": "local-dev-user",
                                "session_id": "session-1",
                            },
                            "source_refs": [
                                {"source_type": "execution", "source_id": "turn-1"}
                            ],
                        },
                        {
                            "memory_id": "22222222-2222-2222-2222-222222222222",
                            "memory_type": "constraint",
                            "content": "Memory from another tenant.",
                            "confidence": 1.0,
                            "importance": 1.0,
                            "scope": {
                                "tenant_id": "other-org",
                                "user_id": "local-dev-user",
                            },
                            "source_refs": [],
                        },
                        {
                            "memory_id": "33333333-3333-3333-3333-333333333333",
                            "memory_type": "profile",
                            "content": "Memory from another user.",
                            "confidence": 1.0,
                            "importance": 1.0,
                            "scope": {
                                "tenant_id": "test-org",
                                "user_id": "other-user",
                            },
                            "source_refs": [],
                        },
                        {"memory_type": "invalid-record"},
                    ],
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        logger = InMemoryRuntimeLogger()
        loader = AxiomExperienceMemoryLoader(
            base_url="http://intent-service.local/",
            limit=20,
            client=client,
            logger=logger,
        )

        try:
            context = await loader.load(
                query="Compare the reports",
                tenant_id="test-org",
                user_id="local-dev-user",
                workspace_id=None,
                agent_id=None,
                session_id="session-1",
                trace_id="trace-1",
            )
        finally:
            await client.aclose()

        self.assertEqual(
            captured["url"],
            "http://intent-service.local/api/v1/memories/search",
        )
        self.assertEqual(
            captured["payload"],
            {
                "tenant_id": "test-org",
                "user_id": "local-dev-user",
                "session_id": "session-1",
                "query": "Compare the reports",
                "memory_types": [
                    "profile",
                    "preference",
                    "constraint",
                    "episodic",
                    "semantic",
                    "outcome",
                    "procedure",
                ],
                "search_type": "hybrid",
                "limit": 20,
                "trace_id": "trace-1",
            },
        )
        self.assertTrue(context.loaded)
        self.assertEqual(context.mode, "active")
        self.assertEqual(len(context.cards), 1)
        self.assertEqual(context.cards[0].content, "Prefer concise Markdown.")
        self.assertEqual(
            [event for event, _ in logger.events],
            ["memory.load.started", "memory.load.completed"],
        )

    async def test_load_fails_open_without_logging_sensitive_content(self) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, text="private upstream failure")
            )
        )
        logger = InMemoryRuntimeLogger()
        loader = AxiomExperienceMemoryLoader(
            base_url="http://intent-service.local",
            client=client,
            logger=logger,
        )

        try:
            context = await loader.load(
                query="Sensitive user query",
                tenant_id="test-org",
                user_id="local-dev-user",
                workspace_id=None,
                agent_id=None,
                session_id=None,
                trace_id="trace-1",
            )
        finally:
            await client.aclose()

        self.assertFalse(context.loaded)
        self.assertEqual(context.mode, "failed")
        self.assertEqual(context.cards, ())
        self.assertEqual(context.error, "HTTPStatusError")
        event, payload = logger.events[-1]
        self.assertEqual(event, "memory.load.failed")
        self.assertEqual(payload["error_type"], "HTTPStatusError")
        self.assertNotIn("Sensitive user query", str(payload))
        self.assertNotIn("private upstream failure", str(payload))


if __name__ == "__main__":
    unittest.main()
