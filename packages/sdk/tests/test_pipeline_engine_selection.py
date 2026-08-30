from __future__ import annotations

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    PreparedExecution,
    SessionContext,
    UserQuery,
)
from data_intelligence_sdk.memory import MemoryContext
from data_intelligence_sdk.registry.engine_registry import InMemoryEngineRegistry
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger
from data_intelligence_sdk.sandbox.artifacts import FilesystemArtifactStore


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


def test_pipeline_builds_selected_files_scope_from_session_context() -> None:
    captured = {}

    class RecordingEngine(_GeneralEngine):
        def run(self, input: EngineInput) -> EngineOutput:
            captured["scope"] = input.runtime.selected_files_scope
            return super().run(input)

    registry = InMemoryEngineRegistry()
    registry.register(RecordingEngine())
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=registry,
    )
    spec = ExecutionSpec(
        intent="general",
        objective="Inspect the file.",
        confirmed=True,
        engine_hint="general",
    )
    prepared = PreparedExecution(
        query=UserQuery(text="Inspect the file."),
        intent="general",
        spec=spec,
        session_context=SessionContext(
            state={
                "selected_files": {
                    "mode": "selected",
                    "resource_ids": ["document-1", "document-2"],
                }
            }
        ),
    )

    pipeline.execute_confirmed_spec(prepared, spec, memory_context=MemoryContext())

    assert captured["scope"].document_ids == ("document-1", "document-2")


def test_pipeline_passes_workspace_id_to_engine_runtime_context() -> None:
    captured = {}

    class RecordingEngine(_GeneralEngine):
        def run(self, input: EngineInput) -> EngineOutput:
            captured["workspace_id"] = input.runtime.workspace_id
            return super().run(input)

    registry = InMemoryEngineRegistry()
    registry.register(RecordingEngine())
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=registry,
        workspace_id="workspace-1",
    )
    spec = ExecutionSpec(
        intent="general",
        objective="Inspect the selected file.",
        confirmed=True,
        engine_hint="general",
    )
    prepared = PreparedExecution(
        query=UserQuery(text="Inspect the selected file."),
        intent="general",
        spec=spec,
    )

    pipeline.execute_confirmed_spec(prepared, spec, memory_context=MemoryContext())

    assert captured["workspace_id"] == "workspace-1"


def test_standalone_confirmed_execution_creates_a_run_artifact(tmp_path) -> None:
    registry = InMemoryEngineRegistry()
    registry.register(_GeneralEngine())
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=registry,
        artifact_store=FilesystemArtifactStore(tmp_path),
    )
    spec = ExecutionSpec(
        intent="general",
        objective="Inspect the selected file.",
        confirmed=True,
        engine_hint="general",
    )
    prepared = PreparedExecution(
        query=UserQuery(text="Inspect the selected file."),
        intent="general",
        spec=spec,
    )

    pipeline.execute_confirmed_spec(
        prepared,
        spec,
        memory_context=MemoryContext(),
    )

    assert prepared.run_artifact is not None


def test_standalone_stream_execution_creates_a_run_artifact(tmp_path) -> None:
    registry = InMemoryEngineRegistry()
    registry.register(_GeneralEngine())
    pipeline = DataIntelligencePipeline(
        intent_analyzer=object(),
        spec_builder=object(),
        spec_confirmation=object(),
        engine_registry=registry,
        artifact_store=FilesystemArtifactStore(tmp_path),
    )
    spec = ExecutionSpec(
        intent="general",
        objective="Inspect the selected file.",
        confirmed=True,
        engine_hint="general",
    )
    prepared = PreparedExecution(
        query=UserQuery(text="Inspect the selected file."),
        intent="general",
        spec=spec,
    )

    responses = list(
        pipeline.stream_confirmed_spec(
            prepared,
            spec,
            memory_context=MemoryContext(),
        )
    )

    assert prepared.run_artifact is not None
    assert responses[-1].metadata["engine_name"] == "general"
