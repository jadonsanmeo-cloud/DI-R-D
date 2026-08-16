import tempfile
import unittest
from pathlib import Path

import httpx

from data_intelligence_api.app.factory import create_app
from data_intelligence_api.application.query_orchestrator import (
    DelegateToDataFlow,
    DirectGeneralAnswer,
)
from data_intelligence_api.infrastructure.config.settings import ApiSettings
from data_intelligence_api.infrastructure.persistence.memory.run_repository import (
    InMemoryRunRepository,
)
from data_intelligence_sdk.memory import MemoryContext


class CountingMemoryLoader:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.context = MemoryContext(loaded=True, mode="active")

    async def load(self, **kwargs: object) -> MemoryContext:
        self.calls.append(kwargs)
        return self.context


class RecordingOrchestrator:
    def __init__(self, result: object) -> None:
        self.result = result
        self.memory_contexts: list[MemoryContext | None] = []

    async def route(
        self,
        query: object,
        session_context: object = None,
        *,
        memory_context: MemoryContext | None = None,
    ) -> object:
        self.memory_contexts.append(memory_context)
        return self.result


class MemoryResponseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_request_loads_memory_once_before_routing(self) -> None:
        await self._assert_request_loads_once(
            DirectGeneralAnswer("A direct general answer."),
        )

    async def test_delegated_request_reuses_memory_without_second_load(self) -> None:
        await self._assert_request_loads_once(DelegateToDataFlow())

    async def _assert_request_loads_once(self, route_result: object) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            loader = CountingMemoryLoader()
            orchestrator = RecordingOrchestrator(route_result)
            settings = ApiSettings(
                Path(temp_dir),
                ("http://localhost:3000",),
                5,
                memory_enabled=True,
                memory_tenant_id="test-org",
                memory_default_user_id="local-dev-user",
            )
            app = create_app(
                settings,
                pipeline_factory=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("Stop after delegated routing.")
                ),
                run_repository=InMemoryRunRepository(),
                query_orchestrator=orchestrator,
                memory_loader=loader,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/responses",
                    json={
                        "input": "Compare the reports",
                        "user_id": "request-user",
                        "session_id": "session-1",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(loader.calls), 1)
        self.assertEqual(loader.calls[0]["tenant_id"], "test-org")
        self.assertEqual(loader.calls[0]["user_id"], "request-user")
        self.assertEqual(loader.calls[0]["session_id"], "session-1")
        self.assertEqual(len(orchestrator.memory_contexts), 1)
        self.assertIs(orchestrator.memory_contexts[0], loader.context)

    async def test_upstream_context_skips_legacy_memory_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            loader = CountingMemoryLoader()
            orchestrator = RecordingOrchestrator(
                DirectGeneralAnswer("A direct general answer.")
            )
            settings = ApiSettings(Path(temp_dir), ("http://localhost:3000",), 5)
            app = create_app(
                settings,
                run_repository=InMemoryRunRepository(),
                query_orchestrator=orchestrator,
                memory_loader=loader,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/responses",
                    json={
                        "input": "What is PostgreSQL?",
                        "session_id": "session-1",
                        "memory_context": {
                            "source": "intelligence-service",
                            "cards": [{
                                "memory_id": "m1",
                                "memory_type": "preference",
                                "content": "Prefer concise answers.",
                                "confidence": 0.9,
                                "importance": 0.8,
                            }],
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(loader.calls, [])
        self.assertIsNotNone(orchestrator.memory_contexts[0])
        self.assertEqual(orchestrator.memory_contexts[0].mode, "upstream")


if __name__ == "__main__":
    unittest.main()
