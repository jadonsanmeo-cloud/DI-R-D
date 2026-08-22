from __future__ import annotations

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    PreparedExecution,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger


class _GeneralEngine:
    name = "general"
    description = "General-purpose test engine."

    def run(self, input: EngineInput) -> EngineOutput:
        del input
        return EngineOutput(engine_name=self.name, answer="Hello")


def test_pipeline_records_selected_engine_and_selection_source() -> None:
    registry = InMemoryEngineRegistry()
    registry.register(_GeneralEngine())
    logger = InMemoryRuntimeLogger()
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=registry,
        logger=logger,
    )
    spec = ExecutionSpec(
        intent="general",
        objective="Hello",
        confirmed=True,
        engine_hint="general",
    )
    prepared = PreparedExecution(
        query=UserQuery(text="Hello"),
        intent="general",
        spec=spec,
    )

    response = pipeline.execute_confirmed_spec(
        prepared,
        spec,
        memory_context=MemoryContext(),
    )

    assert (
        "runtime.engine.selected",
        {"engine_name": "general", "selection_source": "explicit"},
    ) in logger.events
    assert response.metadata["engine_name"] == "general"
    assert response.metadata["engine_selection_source"] == "explicit"
