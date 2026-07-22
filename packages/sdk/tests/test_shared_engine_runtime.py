from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import unittest
from typing import Any

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report_shiny import ReportEngine
from data_intelligence_sdk.runtime.sandbox import (
    EngineSandboxSession,
    SandboxEnvironment,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class _CapturingCodeAgent:
    def __init__(self) -> None:
        self.scoped_payload: dict[str, Any] | None = None

    def run(
        self,
        step: dict[str, Any],
        scoped_payload: dict[str, Any],
        error_logs: str | None = None,
        validation_feedback: str | None = None,
    ) -> dict[str, Any]:
        del step, error_logs, validation_feedback
        self.scoped_payload = scoped_payload
        return {
            "tool_name": "generated_report_step",
            "parameters_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
            "execution_arguments": {},
            "source_code": "def generated_report_step():\n    return []\n",
        }


class SharedEngineRuntimeTests(unittest.TestCase):
    def test_shared_sandbox_session_is_decoupled_from_deep_agents(self) -> None:
        import_check = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "from data_intelligence_sdk.runtime.sandbox import "
                    "EngineSandboxSession; "
                    "assert 'deepagents.backends.protocol' not in sys.modules; "
                    "assert EngineSandboxSession"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(import_check.returncode, 0, import_check.stderr)
        sandbox_spec = importlib.util.find_spec(
            "data_intelligence_sdk.runtime.sandbox"
        )
        self.assertIsNotNone(sandbox_spec)
        sandbox_module = importlib.import_module(
            "data_intelligence_sdk.runtime.sandbox"
        )
        backend_module = importlib.import_module(
            "data_intelligence_sdk.runtime.deep_agent_backend"
        )

        session = sandbox_module.EngineSandboxSession(sandbox=object())
        self.assertFalse(hasattr(session, "backend"))

        backend = backend_module.DeepAgentSandboxBackend(session)
        self.assertIs(backend.session, session)

    def test_engine_runtime_exposes_request_sandbox_environment(self) -> None:
        environment = SandboxEnvironment(
            runtime="python",
            dependency_management="axiom_sandbox_service",
            network_access=False,
            source_access="read_only",
        )
        runtime = EngineRuntimeContext(
            sandbox=EngineSandboxSession(
                sandbox=object(),
                environment=environment,
            )
        )

        self.assertIs(runtime.sandbox_environment, environment)
        self.assertEqual(
            runtime.sandbox_environment.to_prompt_payload(),
            {
                "runtime": "python",
                "dependency_management": "axiom_sandbox_service",
                "network_access": False,
                "source_access": "read_only",
            },
        )

    def test_report_code_generation_uses_runtime_sandbox_environment(self) -> None:
        environment = SandboxEnvironment(
            runtime="python",
            dependency_management="axiom_sandbox_service",
            network_access=True,
            source_access="read_write",
        )
        runtime = EngineRuntimeContext(
            sandbox=EngineSandboxSession(
                sandbox=object(),
                environment=environment,
            )
        )
        engine = ReportEngine(llm=None)
        code_agent = _CapturingCodeAgent()
        engine.code_agent = code_agent

        engine._data_generate_code(
            {
                "step": {"step_id": "step-1", "description": "Generate data"},
                "spec": ExecutionSpec(intent="report", objective="Build a report"),
                "corpus_package": DataCorpusPackage(),
                "runtime": runtime,
                "resolved_inputs": [],
            }
        )

        self.assertIsNotNone(code_agent.scoped_payload)
        assert code_agent.scoped_payload is not None
        self.assertEqual(
            code_agent.scoped_payload["sandbox_environment"],
            {
                "runtime": "python",
                "dependency_management": "axiom_sandbox_service",
                "network_access": True,
                "source_access": "read_write",
                "materialization_contract": {
                    "format": "json_serializable",
                    "natural_record_shapes": [
                        "table_rows",
                        "spreadsheet_rows",
                        "document_pages",
                        "text_chunks",
                        "metadata_records",
                    ],
                },
            },
        )
