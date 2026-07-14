from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.core.types import CapabilityRequirement, DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.methods.csv import register_csv_methods
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.method_hub import MethodHub


class RecordingAgent:
    def __init__(self, tool_calls: list[dict[str, object]]) -> None:
        self.tool_calls = tool_calls
        self.prompts: list[str] = []
        self.tools: list[dict[str, object]] = []

    def run(self, *, prompt: str, tools: dict[str, object]) -> dict[str, object]:
        self.prompts.append(prompt)
        self.tools.append(tools)
        return {"tool_calls": self.tool_calls, "final_answer": "done"}


def untrusted_lookup(path: str) -> dict[str, str]:
    return {"path": path}


class GeneralEngineMethodSelectionTests(unittest.TestCase):
    def test_engine_uses_requirement_driven_tool_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text("country,status,revenue\nUS,complete,10\n", encoding="utf-8")

            hub = MethodHub()
            register_csv_methods(hub)
            hub.register(
                "untrusted_lookup",
                untrusted_lookup,
                capability_names=["inspect_data"],
                trust_level="generated_unvalidated",
            )
            runtime = EngineRuntimeContext(method_hub=hub)
            agent = RecordingAgent(
                tool_calls=[{"name": "scan_csv", "args": {"path": str(csv_path)}}]
            )

            output = GeneralPurposeEngine(llm=agent).run(
                ExecutionSpec(
                    intent="reason",
                    objective="What columns are in this file?",
                    capability_requirements=[CapabilityRequirement(name="inspect_data")],
                ),
                DataCorpusPackage(sources=[str(csv_path)]),
                runtime,
            )

        self.assertEqual(output.result, "done")
        self.assertEqual(set(agent.tools[0]), {"scan_csv"})
        self.assertNotIn("filter_csv", agent.tools[0])
        self.assertNotIn("untrusted_lookup", agent.tools[0])
        self.assertEqual(output.trace.method_calls[0].method_name, "scan_csv")


if __name__ == "__main__":
    unittest.main()
