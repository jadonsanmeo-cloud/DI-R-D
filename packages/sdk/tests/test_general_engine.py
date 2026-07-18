from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.deep_agent_sandbox import DeepAgentSandboxSession
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession


@dataclass
class FakeMessage:
    content: object


class SequencedAgent:
    def __init__(
        self,
        results: list[dict[str, object]],
        callbacks: list[Callable[[], None] | None] | None = None,
    ) -> None:
        self.results = list(results)
        self.callbacks = list(callbacks or [])
        self.invocations: list[dict[str, object]] = []

    def invoke(self, payload: dict[str, object]) -> dict[str, object]:
        index = len(self.invocations)
        self.invocations.append(payload)
        if index < len(self.callbacks) and self.callbacks[index] is not None:
            callback = self.callbacks[index]
            assert callback is not None
            callback()
        return self.results[index]


class FakeSynthesisModel:
    model_name = "fake-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.invocations: list[object] = []

    def invoke(self, messages: object) -> FakeMessage:
        self.invocations.append(messages)
        return FakeMessage(self.content)


def make_spec() -> ExecutionSpec:
    return ExecutionSpec(
        intent="reason",
        objective="Explain the document",
        data_requirements=["document.md"],
        confirmed=True,
    )


def make_corpus() -> DataCorpusPackage:
    return DataCorpusPackage(sources=["document.md"])


def make_runtime() -> EngineRuntimeContext:
    sandbox = DeepAgentSandboxSession(
        sandbox=object(),
        source_paths={"document.md": "/workspace/input/document.md"},
    )
    return EngineRuntimeContext(
        sandbox=sandbox,
        run_artifact=cast(RunArtifactSession, object()),
    )


def record_success(
    runtime: EngineRuntimeContext,
    result: object,
    *,
    stdout: str = "",
) -> None:
    runtime.run_context.record_method_call(
        "execute_python",
        status="completed",
        outputs={
            "success": True,
            "result": result,
            "stdout": stdout,
            "stderr": "",
        },
    )


def make_engine(
    agent: SequencedAgent,
    model: FakeSynthesisModel,
) -> GeneralPurposeEngine:
    return GeneralPurposeEngine(
        llm=model,
        agent_factory=lambda **_kwargs: agent,
    )


def run_engine(
    engine: GeneralPurposeEngine,
    runtime: EngineRuntimeContext,
):
    with patch.object(engine, "_register_minimal_profile"):
        return engine.run(make_spec(), make_corpus(), runtime)


class GeneralPurposeEngineRecoveryTests(unittest.TestCase):
    def test_non_empty_answer_with_successful_execution_returns_without_retry(
        self,
    ) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [{"messages": [FakeMessage("Document summary")]}],
            callbacks=[lambda: record_success(runtime, {"topic": "planning"})],
        )
        model = FakeSynthesisModel("unused")
        engine = make_engine(agent, model)

        output = run_engine(engine, runtime)

        self.assertEqual(output.result, "Document summary")
        self.assertEqual(len(agent.invocations), 1)
        self.assertEqual(model.invocations, [])

    def test_blank_result_without_tool_retries_once(self) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [
                {"messages": [FakeMessage("")]},
                {"messages": [FakeMessage("Recovered summary")]},
            ],
            callbacks=[
                None,
                lambda: record_success(runtime, {"topic": "planning"}),
            ],
        )
        engine = make_engine(agent, FakeSynthesisModel("unused"))

        output = run_engine(engine, runtime)

        self.assertEqual(output.result, "Recovered summary")
        self.assertEqual(len(agent.invocations), 2)

    def test_blank_terminal_messages_synthesize_successful_execution(self) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [
                {"messages": [FakeMessage("")]},
                {"messages": [FakeMessage("")]},
            ],
            callbacks=[
                lambda: record_success(runtime, {"topic": "planning"}),
                None,
            ],
        )
        model = FakeSynthesisModel("Synthesized summary")
        engine = make_engine(agent, model)

        output = run_engine(engine, runtime)

        self.assertEqual(output.result, "Synthesized summary")
        self.assertEqual(len(agent.invocations), 2)
        self.assertEqual(len(model.invocations), 1)
        step_names = [step.name for step in runtime.run_context.trace.steps]
        self.assertIn("deep_agent_retry_started", step_names)
        self.assertIn("deep_agent_retry_completed", step_names)
        self.assertIn("deep_agent_fallback_synthesis", step_names)

    def test_blank_synthesis_renders_successful_execution_result(self) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [
                {"messages": [FakeMessage("")]},
                {"messages": [FakeMessage("")]},
            ],
            callbacks=[
                lambda: record_success(runtime, {"topic": "planning"}),
                None,
            ],
        )
        engine = make_engine(agent, FakeSynthesisModel(""))

        output = run_engine(engine, runtime)

        self.assertIn('"topic": "planning"', output.result)
        self.assertEqual(len(agent.invocations), 2)

    def test_two_attempts_without_successful_execution_fail(self) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [
                {"messages": [FakeMessage("")]},
                {"messages": [FakeMessage("")]},
            ]
        )
        engine = make_engine(agent, FakeSynthesisModel("unused"))

        with self.assertRaisesRegex(RuntimeError, "successful execute_python"):
            run_engine(engine, runtime)

        self.assertEqual(len(agent.invocations), 2)


if __name__ == "__main__":
    unittest.main()
