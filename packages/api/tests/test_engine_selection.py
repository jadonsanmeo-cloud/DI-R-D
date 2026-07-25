import unittest
from dataclasses import dataclass
from unittest.mock import Mock, patch

import httpx

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    QueryAIRemoteReasonEngine,
    create_example_pipeline,
)
from data_intelligence_api.application.workflow import ENGINE_ROUTE_MAP
from data_intelligence_sdk.core.types import EngineInput, ExecutionSpec, UserQuery
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.registry.engine_selector import EngineDescriptor
from data_intelligence_sdk.runtime.config import OpenRouterSettings, SandboxSettings


@dataclass
class FakeEngine:
    name: str
    description: str

    def can_handle(self, spec: ExecutionSpec) -> bool:
        del spec
        return True


class StaticSelector:
    def select(
        self,
        spec: ExecutionSpec,
        engines: tuple[EngineDescriptor, ...],
    ) -> str:
        del spec, engines
        return "general_purpose"

class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "run_id": "019f98e5-914f-78af-8fce-8490b86fe0dd",
            "final_answer": "Remote answer",
            "evidence": ["reports/annual-report.pdf", "tables/revenue.csv"],
        }

class FakeHTTPClient:
    def __init__(self, *, timeout):
        self.timeout = timeout
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, endpoint, *, json):
        self.posts.append((endpoint, json))
        return FakeResponse()


class EngineSelectionFactoryTests(unittest.TestCase):
    def test_remote_reason_engine_posts_query_and_spec_strings(self) -> None:
        client = FakeHTTPClient(timeout=300.0)
        spec = ExecutionSpec(intent="reason", objective="Find revenue evidence")

        with patch(
            "data_intelligence_api.infrastructure.workflow.pipeline_factory.httpx.Client",
            return_value=client,
        ):
            output = QueryAIRemoteReasonEngine(endpoint="http://queryai/query").run(
                EngineInput(
                    query=UserQuery(text="Find revenue evidence"),
                    spec=spec,
                    runtime=EngineRuntimeContext(),
                )
            )

        self.assertEqual(
            client.posts[0][0],
            "http://queryai/query",
        )
        self.assertEqual(client.posts[0][1]["query"], "Find revenue evidence")
        self.assertIsInstance(client.posts[0][1]["spec"], str)
        self.assertEqual(output.answer, "Remote answer")
        self.assertEqual(
            output.evidence.sources,
            ["reports/annual-report.pdf", "tables/revenue.csv"],
        )
        self.assertEqual(
            output.metadata["queryai_run_id"],
            "019f98e5-914f-78af-8fce-8490b86fe0dd",
        )

    def test_remote_reason_engine_reports_unreachable_endpoint(self) -> None:
        spec = ExecutionSpec(intent="reason", objective="Find revenue evidence")

        with patch(
            "data_intelligence_api.infrastructure.workflow.pipeline_factory.httpx.Client"
        ) as client_type:
            client_type.return_value.__enter__.return_value.post.side_effect = (
                httpx.ConnectError("connection refused")
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "QueryAI reason engine request failed",
            ):
                QueryAIRemoteReasonEngine(endpoint="http://queryai/query").run(
                    EngineInput(
                        query=UserQuery(text="Find revenue evidence"),
                        spec=spec,
                        runtime=EngineRuntimeContext(),
                    )
                )

    def test_frontend_engine_values_route_to_distinct_engines(self) -> None:
        self.assertEqual(ENGINE_ROUTE_MAP["general"], "general_purpose")
        self.assertEqual(ENGINE_ROUTE_MAP["reason"], "reason")
        self.assertEqual(ENGINE_ROUTE_MAP["report"], "report")

    def test_default_pipeline_registers_builtin_and_remote_engines(self) -> None:
        manager = Mock()
        manager.sandbox_settings.return_value = SandboxSettings(enabled=False)
        general = FakeEngine("general_purpose", "General analysis engine")
        reason = FakeEngine("reason", "Remote QueryAI reason engine")
        report = FakeEngine("report", "Structured report engine")

        with (
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory."
                "GeneralPurposeEngine",
                return_value=general,
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory."
                "QueryAIRemoteReasonEngine",
                return_value=reason,
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.ReportEngine",
                return_value=report,
            ),
        ):
            pipeline = create_example_pipeline(
                config_manager=manager,
                spec_builder=object(),
                engine_selector=StaticSelector(),
                artifact_store=object(),
            )

        self.assertEqual(
            pipeline.engine_registry.descriptors(),
            (
                EngineDescriptor("general_purpose", "General analysis engine"),
                EngineDescriptor("reason", "Remote QueryAI reason engine"),
                EngineDescriptor("report", "Structured report engine"),
            ),
        )

    def test_selector_reuses_spec_builder_openrouter_client(self) -> None:
        manager = Mock()
        manager.openrouter_settings.return_value = OpenRouterSettings(
            model="selector-model",
            api_key="secret",
            base_url="https://models.example/v1",
        )
        manager.sandbox_settings.return_value = SandboxSettings(enabled=False)
        shared_client = object()
        selector = StaticSelector()

        with (
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.OpenAICompatibleLLMClient",
                return_value=shared_client,
            ) as client_type,
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.LLMSpecBuilder",
                return_value=object(),
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.LLMEngineSelector",
                return_value=selector,
            ) as selector_type,
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory."
                "GeneralPurposeEngine",
                return_value=FakeEngine(
                    "general_purpose",
                    "General analysis engine",
                ),
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory."
                "QueryAIRemoteReasonEngine",
                return_value=FakeEngine(
                    "reason",
                    "Remote QueryAI reason engine",
                ),
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.ReportEngine",
                return_value=FakeEngine("report", "Structured report engine"),
            ),
        ):
            create_example_pipeline(
                config_manager=manager,
                use_llm_spec_builder=True,
                artifact_store=object(),
            )

        client_type.assert_called_once_with(
            base_url="https://models.example/v1",
            api_key="secret",
            model="selector-model",
        )
        selector_type.assert_called_once_with(shared_client)


if __name__ == "__main__":
    unittest.main()
