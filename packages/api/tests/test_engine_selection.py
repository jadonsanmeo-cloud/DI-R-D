import unittest
from dataclasses import dataclass
from unittest.mock import Mock, patch

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)
from data_intelligence_sdk.core.types import ExecutionSpec
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


class EngineSelectionFactoryTests(unittest.TestCase):
    def test_default_pipeline_registers_both_builtin_engines(self) -> None:
        manager = Mock()
        manager.sandbox_settings.return_value = SandboxSettings(enabled=False)
        general = FakeEngine("general_purpose", "General analysis engine")
        report = FakeEngine("report", "Structured report engine")

        with (
            patch(
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.GeneralPurposeEngine",
                return_value=general,
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
                "data_intelligence_api.infrastructure.workflow.pipeline_factory.GeneralPurposeEngine",
                return_value=FakeEngine("general_purpose", "General analysis engine"),
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
