from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import dataclass
from inspect import signature
from typing import cast
from unittest.mock import patch

from data_intelligence_sdk.core.types import EngineInput, ExecutionSpec, UserQuery
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.sandbox import EngineSandboxSession
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


def make_runtime() -> EngineRuntimeContext:
    sandbox = EngineSandboxSession(
        sandbox=object(),
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
    spec = make_spec()
    with patch.object(engine, "_register_minimal_profile"):
        return engine.run(
            EngineInput(
                query=UserQuery(text=spec.objective),
                spec=spec,
                runtime=runtime,
            )
        )


class GeneralPurposeEngineRecoveryTests(unittest.TestCase):
    def test_run_accepts_engine_input(self) -> None:
        parameters = list(signature(GeneralPurposeEngine.run).parameters)

        self.assertEqual(parameters, ["self", "input"])

    def test_system_prompt_accepts_runtime_without_sources(self) -> None:
        parameters = list(signature(GeneralPurposeEngine._system_prompt).parameters)

        self.assertEqual(parameters, ["self", "spec", "runtime"])

    def test_system_prompt_does_not_describe_sandbox_files(self) -> None:
        engine = object.__new__(GeneralPurposeEngine)

        prompt = engine._system_prompt(make_spec(), EngineRuntimeContext())

        self.assertNotIn("Staged sources", prompt)
        self.assertNotIn("sandbox_path", prompt)

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

    def test_two_blank_attempts_without_execution_fail(self) -> None:
        runtime = make_runtime()
        agent = SequencedAgent(
            [
                {"messages": [FakeMessage("")]},
                {"messages": [FakeMessage("")]},
            ]
        )
        engine = make_engine(agent, FakeSynthesisModel("unused"))

        with self.assertRaisesRegex(RuntimeError, "no usable answer"):
            run_engine(engine, runtime)

        self.assertEqual(len(agent.invocations), 2)


if __name__ == "__main__":
    unittest.main()
