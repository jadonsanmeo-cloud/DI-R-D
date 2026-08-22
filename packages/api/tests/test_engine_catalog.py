from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)
from data_intelligence_sdk.core.types import EngineInput, EngineOutput
from data_intelligence_sdk.registry.engine_selector import LLMEngineSelector
from data_intelligence_sdk.runtime.config import OpenRouterSettings


class _GeneralLLM:
    def invoke(self, messages: list[object]) -> object:
        del messages
        raise AssertionError("Catalog construction must not invoke the general LLM.")


class _StaticSelector:
    def select(self, request: object, engines: object) -> str:
        del request, engines
        return "general"


class _ReportEngine:
    name = "report"
    description = "Structured report generation test engine."

    def run(self, input: EngineInput) -> EngineOutput:
        del input
        return EngineOutput(engine_name=self.name, answer="Report")

    def run_markdown(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("Catalog construction must not run the report engine.")


def test_default_catalog_exposes_only_public_engine_names() -> None:
    pipeline = create_example_pipeline(
        llm=_GeneralLLM(),
        engine_selector=_StaticSelector(),  # type: ignore[arg-type]
        markdown_report_engine=_ReportEngine(),
        configure_default_sandbox=False,
    )

    assert [item.name for item in pipeline.engine_registry.descriptors()] == [
        "general",
        "reason",
        "report",
    ]


def test_engine_selector_uses_configured_routing_model_not_request_model(
    tmp_path,
) -> None:
    configured_model = "qwen/qwen3.7-flash"
    request_model = "cohere/north-mini-code:free"
    created_models: list[str | None] = []

    class _ConfigManager:
        def artifact_settings(self):
            return SimpleNamespace(root=str(tmp_path / "artifacts"))

        def openrouter_settings(self):
            return OpenRouterSettings(
                model=configured_model,
                api_key="test-key",
                base_url="https://example.test/v1",
            )

    class _RecordingLLMClient:
        def __init__(self, *, model=None, **kwargs):
            del kwargs
            self.model = model
            created_models.append(model)

    with patch(
        "data_intelligence_api.infrastructure.workflow.pipeline_factory."
        "OpenAICompatibleLLMClient",
        _RecordingLLMClient,
    ):
        pipeline = create_example_pipeline(
            llm=_GeneralLLM(),
            model=request_model,
            config_manager=_ConfigManager(),
            use_llm_spec_builder=True,
            configure_default_sandbox=False,
        )

    selector = pipeline.engine_registry._selector
    assert isinstance(selector, LLMEngineSelector)
    assert selector.llm_client.model == configured_model
    assert created_models == [request_model, configured_model]
