from __future__ import annotations

import unittest
from inspect import signature

from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.engines import (
    Engine,
    EngineInput as PublicEngineInput,
    EngineOutput as PublicEngineOutput,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class EngineBaseTests(unittest.TestCase):
    def test_engine_input_groups_runtime_execution_state(self) -> None:
        query = UserQuery(text="Explain the notes")
        spec = ExecutionSpec(intent="general", objective="Explain")
        runtime = EngineRuntimeContext()
        user_context = UserContext(user_id="u-1")

        engine_input = EngineInput(
            query=query,
            spec=spec,
            runtime=runtime,
            user_context=user_context,
        )

        self.assertIs(engine_input.query, query)
        self.assertIs(engine_input.spec, spec)
        self.assertIs(engine_input.runtime, runtime)
        self.assertIs(engine_input.user_context, user_context)

    def test_engine_input_and_output_are_public_engine_contracts(self) -> None:
        self.assertIs(PublicEngineInput, EngineInput)
        self.assertIs(PublicEngineOutput, EngineOutput)

    def test_engine_protocol_run_accepts_engine_input(self) -> None:
        parameters = list(signature(Engine.run).parameters)

        self.assertEqual(parameters, ["self", "input"])


if __name__ == "__main__":
    unittest.main()
