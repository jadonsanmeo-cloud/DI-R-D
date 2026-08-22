from __future__ import annotations

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)
from data_intelligence_sdk.core.types import EngineInput, EngineOutput


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
