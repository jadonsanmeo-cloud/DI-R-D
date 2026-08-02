import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineInput,
    EngineOutput,
    EngineTrace,
    EvidenceBundle,
    ExecutionSpec,
    InterfaceDefinition,
    PreparedExecution,
    UserQuery,
)
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.engines.report import (
    CodeAgent,
    DataScienceProcessor,
    PlanAgent,
    ReportEngine,
    ReportFormatRegistry,
    RouterAgent,
    SemanticAnalysisAgent,
    _StepInputResolver,
    _StepOutputRegistry,
    _normalize_generated_source,
    _normalize_plan_outputs,
)
from data_intelligence_sdk.engines.reporting.contracts import (
    ReportContractValidator,
    SEMANTIC_ANALYSIS_ROUTE,
    ToolArgumentBinder,
)
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.report_sandbox_executor import (
    RequestSandboxExecutor,
)
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession
from data_intelligence_sdk.sandbox.executor import SandboxRunResult


class _FakeEngine:
    name = "report"

    def run(self, input):
        del input
        return EngineOutput(
            engine_name=self.name,
            answer="<html><body>Report</body></html>",
            result="<html><body>Report</body></html>",
            evidence=EvidenceBundle(sources=["sales.csv"]),
            trace=EngineTrace(),
            metadata={
                "report_format": "html",
                "rendered_reports": [
                    {
                        "format": "html",
                        "media_type": "text/html",
                        "content": "<html><body>Report</body></html>",
                    }
                ],
            },
        )


class _FakeRegistry:
    def __init__(self, engine):
        self.engine = engine

    def select(self, spec):
        del spec
        return self.engine


class _FakeSandboxSession:
    source_paths = {"C:\\uploads\\sales.csv": "/workspace/input/sales.csv"}

    def __init__(self):
        self.code = ""

    def execute_python(self, code, run_artifact, *, timeout_seconds=120):
        del run_artifact, timeout_seconds
        self.code = code
        return {
            "success": True,
            "status": "completed",
            "result": {"count": 3},
            "code_artifact_ref": "artifact://code",
            "execution_artifact_ref": "artifact://execution",
            "sandbox_id": "sandbox-1",
            "command_id": "command-1",
            "exit_code": 0,
        }

    def reprovision(self):
        return False


class _ReprovisioningSandboxSession(_FakeSandboxSession):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.reprovision_calls = 0

    def execute_python(self, code, run_artifact, *, timeout_seconds=120):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sandbox is not running")
        return super().execute_python(
            code,
            run_artifact,
            timeout_seconds=timeout_seconds,
        )

    def reprovision(self):
        self.reprovision_calls += 1
        return True


class _WritableSandbox:
    def __init__(self):
        self.files = {}

    def write(self, path, content):
        self.files[path] = content


class _RuntimeSandbox:
    def __init__(self):
        self.sandbox = _WritableSandbox()

    def write(self, path, content):
        self.sandbox.write(path, content)


class _GeneratedRouteAgent:
    def run(self, step, inventory, sources):
        del step, inventory, sources
        return {
            "route": "generate_tool",
            "tool_name": None,
            "arguments": {},
            "reason": "test",
        }


class _ExplodingRouterAgent:
    def run(self, step, inventory, sources):
        del step, inventory, sources
        raise AssertionError("RouterAgent must be bypassed in force_code_agent mode.")


class _RecordingSemanticRouter(RouterAgent):
    def __init__(self):
        super().__init__(None)
        self.input_contracts = []

    def run(
        self,
        step,
        inventory,
        sources,
        resolved_input_contracts=None,
        routing_feedback=None,
        excluded_tool_names=None,
    ):
        del step, inventory, sources, routing_feedback, excluded_tool_names
        self.input_contracts = resolved_input_contracts or []
        return {
            "route": SEMANTIC_ANALYSIS_ROUTE,
            "tool_name": None,
            "arguments": {},
            "argument_bindings": {},
            "reason": "The step requires grounded interpretation.",
        }


class _SemanticExecutionAgent:
    def run(
        self,
        step,
        resolved_inputs,
        template_requirements,
        user_goal,
        validation_feedback=None,
    ):
        del step, template_requirements, user_goal, validation_feedback
        return {
            "status": "completed",
            "output": [
                {
                    "finding": "Monthly volume increased while incidents declined.",
                    "source_ref": resolved_inputs[0]["ref"],
                }
            ],
            "evidence_refs": [resolved_inputs[0]["artifact_ref"]],
            "warnings": [],
            "error": None,
            "batch_count": 1,
        }


class _RepairingRouter(RouterAgent):
    def __init__(self):
        super().__init__(None)
        self.calls = []

    def run(
        self,
        step,
        inventory,
        sources,
        resolved_input_contracts=None,
        routing_feedback=None,
        excluded_tool_names=None,
    ):
        del step, inventory, sources, resolved_input_contracts
        self.calls.append(
            {
                "feedback": list(routing_feedback or []),
                "excluded": list(excluded_tool_names or []),
            }
        )
        if len(self.calls) == 1:
            return {
                "route": "existing_tool",
                "tool_name": "metadata_by_dataset",
                "arguments": {},
                "argument_bindings": {},
                "reason": "Initial route.",
            }
        return {
            "route": SEMANTIC_ANALYSIS_ROUTE,
            "tool_name": None,
            "arguments": {},
            "argument_bindings": {},
            "reason": "Repair selected semantic execution.",
        }


class _SumCodeAgent:
    def __init__(self):
        self.calls = 0
        self.feedback = []

    def run(self, step, schema, error_logs=None, validation_feedback=None):
        del step, schema
        self.calls += 1
        self.feedback.append(
            {
                "error_logs": error_logs,
                "validation_feedback": validation_feedback,
            }
        )
        return {
            "tool_name": "sum_rows",
            "parameters_schema": {
                "type": "object",
                "properties": {"rows": {"type": "array"}},
                "required": ["rows"],
            },
            "output_schema": {"type": "array"},
            "execution_arguments": {},
            "source_code": (
                "def sum_rows(rows: list[dict]) -> list[dict]:\n"
                "    return [{'total': sum(row['value'] for row in rows)}]\n"
            ),
        }


class _ConstantCodeAgent(_SumCodeAgent):
    def run(self, step, schema, error_logs=None, validation_feedback=None):
        del step, schema
        self.calls += 1
        self.feedback.append(
            {
                "error_logs": error_logs,
                "validation_feedback": validation_feedback,
            }
        )
        return {
            "tool_name": "produce_total",
            "parameters_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
            "output_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["total"],
                },
            },
            "execution_arguments": {},
            "source_code": (
                "def produce_total() -> list[dict]:\n" "    return [{'total': 5}]\n"
            ),
        }


class _ExecutingSandbox:
    def __init__(self):
        self.validate_calls = 0
        self.run_calls = 0

    def validate(self, interface, inputs, resource_policy=None):
        del resource_policy
        self.validate_calls += 1
        return self._execute(interface, inputs)

    def run(self, interface, inputs, resource_policy=None):
        del resource_policy
        self.run_calls += 1
        return self._execute(interface, inputs)

    @staticmethod
    def _execute(interface, inputs):
        namespace = {}
        exec(str(interface.implementation_ref), namespace)
        result = namespace[interface.name](**inputs)
        return SandboxRunResult(status="completed", result=result)


class _RetryingSandbox:
    def __init__(self):
        self.validate_calls = 0
        self.run_calls = 0

    def validate(self, interface, inputs, resource_policy=None):
        del interface, inputs, resource_policy
        self.validate_calls += 1
        if self.validate_calls == 1:
            return SandboxRunResult(
                status="failed",
                error="Synthetic runtime failure.",
            )
        return SandboxRunResult(
            status="completed",
            result=[{"total": 5}],
        )

    def run(self, interface, inputs, resource_policy=None):
        del interface, inputs, resource_policy
        self.run_calls += 1
        raise AssertionError("Validated generated code must not execute twice.")


class _AlwaysPassValidator:
    def run(self, step_description, source_code, sandbox_logs, sample_data):
        del step_description, source_code, sandbox_logs, sample_data
        return {
            "status": "Pass",
            "feedback": None,
            "validated_code": None,
        }


class ReportWiringTests(unittest.TestCase):
    def test_required_source_content_output_requires_at_least_one_item(self):
        schema = ReportEngine._step_output_schema(
            {
                "step_id": "materialize-document",
                "required": True,
                "outputs": [
                    {
                        "name": "document-content",
                        "shape": "table",
                        "semantic_roles": ["analysis_data", "source_content"],
                    }
                ],
            },
            {"output_schema": {"type": "array"}},
        )

        self.assertEqual(schema["minItems"], 1)
        self.assertIn(
            "at least 1 item",
            ReportEngine._output_contract_errors([], schema)[0],
        )

    def test_optional_analysis_table_may_be_empty(self):
        schema = ReportEngine._step_output_schema(
            {
                "step_id": "optional-analysis",
                "required": False,
                "outputs": [
                    {
                        "name": "rows",
                        "shape": "table",
                        "semantic_roles": ["analysis_data"],
                    }
                ],
            },
            {"output_schema": {"type": "array"}},
        )

        self.assertNotIn("minItems", schema)
        self.assertEqual(ReportEngine._output_contract_errors([], schema), [])

    def test_method_tool_binds_required_upstream_output_by_contract(self):
        resolved = [
            {
                "argument_name": "doc_metadata",
                "output_name": "doc_metadata",
                "source_step_id": "extract-metadata",
                "ref": "step-output://extract-metadata/doc_metadata",
                "semantic_roles": ["document_metadata"],
                "value": {"modality": "pdf"},
            }
        ]
        result = ToolArgumentBinder().bind(
            {
                "arguments": {},
                "argument_bindings": {
                    "records": {
                        "input_ref": (
                            "step-output://extract-metadata/doc_metadata"
                        ),
                        "adapter": "identity",
                    }
                },
            },
            {
                "type": "object",
                "properties": {"records": {"type": "object"}},
                "required": ["records"],
            },
            resolved,
        )

        self.assertEqual(result.errors, ())
        self.assertEqual(result.arguments["records"], {"modality": "pdf"})
        self.assertEqual(
            result.argument_bindings["records"]["input_ref"],
            "step-output://extract-metadata/doc_metadata",
        )

    def test_tool_binder_does_not_treat_artifact_path_as_file_identity(self):
        artifact_path = r"G:\artifacts\run\data\document.json"
        resolved = [
            {
                "ref": "step-output://materialize/content",
                "argument_name": "content",
                "output_name": "content",
                "semantic_roles": ["source_content"],
                "value": [{"text": "Evidence"}],
                "host_path": artifact_path,
                "artifact_ref": "artifact://run/data/document.json",
            }
        ]
        result = ToolArgumentBinder().bind(
            {
                "arguments": {"file_name": artifact_path},
                "argument_bindings": {},
            },
            {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Filename selector for an ingested object.",
                    }
                },
                "required": ["file_name"],
            },
            resolved,
        )

        self.assertIn(
            "expects an identity selector",
            "; ".join(result.errors),
        )
        self.assertNotIn("file_name", result.argument_bindings)

    def test_tool_binder_binds_artifact_only_to_explicit_path_contract(self):
        artifact_path = r"G:\artifacts\run\data\document.json"
        resolved = [
            {
                "ref": "step-output://materialize/content",
                "argument_name": "content",
                "output_name": "content",
                "semantic_roles": ["source_content"],
                "value": [{"text": "Evidence"}],
                "host_path": artifact_path,
            }
        ]
        result = ToolArgumentBinder().bind(
            {"arguments": {}, "argument_bindings": {}},
            {
                "type": "object",
                "properties": {
                    "input_path": {
                        "type": "string",
                        "description": "Local staged JSON file path.",
                    }
                },
                "required": ["input_path"],
            },
            resolved,
        )

        self.assertEqual(result.errors, ())
        self.assertEqual(result.arguments["input_path"], artifact_path)
        self.assertEqual(
            result.argument_bindings["input_path"]["adapter"],
            "artifact_path",
        )

    def test_tool_binder_uses_input_structure_to_reject_wrong_array_items(self):
        result = ToolArgumentBinder().bind(
            {"arguments": {}, "argument_bindings": {}},
            {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                    }
                },
                "required": ["values"],
            },
            [
                {
                    "ref": "step-output://source/records",
                    "argument_name": "records",
                    "value": [{"text": "Evidence"}],
                    "json_type": "array",
                    "structure": {
                        "type": "array",
                        "item": {"type": "object"},
                    },
                }
            ],
        )

        self.assertNotIn("values", result.arguments)
        self.assertIn("is unbound", "; ".join(result.errors))

    def test_plan_output_mapping_form_is_normalized_without_synthetic_output(self):
        outputs = _normalize_plan_outputs(
            [
                {
                    "evidence": {
                        "type": "list",
                        "shape": {"item_type": "object"},
                        "semantic_roles": ["goal_evidence"],
                    }
                }
            ],
            "extract",
        )

        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["name"], "evidence")
        self.assertEqual(outputs[0]["type"], "array")
        self.assertEqual(outputs[0]["semantic_roles"], ["goal_evidence"])

    def test_tool_binder_maps_different_names_from_type_and_semantics(self):
        result = ToolArgumentBinder().bind(
            {"arguments": {}, "argument_bindings": {}},
            {
                "type": "object",
                "properties": {"records": {"type": "array"}},
                "required": ["records"],
            },
            [
                {
                    "ref": "step-output://materialize/document_records",
                    "argument_name": "document_records",
                    "output_name": "document_records",
                    "source_step_id": "materialize",
                    "semantic_roles": ["source_content"],
                    "value": [{"text": "Evidence"}],
                }
            ],
        )

        self.assertEqual(result.errors, ())
        self.assertEqual(result.arguments["records"], [{"text": "Evidence"}])
        self.assertEqual(
            result.argument_bindings["records"]["adapter"],
            "identity",
        )

    def test_tool_binder_does_not_guess_between_ambiguous_inputs(self):
        result = ToolArgumentBinder().bind(
            {"arguments": {}, "argument_bindings": {}},
            {
                "type": "object",
                "properties": {"records": {"type": "array"}},
                "required": ["records"],
            },
            [
                {
                    "ref": "step-output://left/data",
                    "argument_name": "data",
                    "output_name": "data",
                    "source_step_id": "left",
                    "semantic_roles": ["analysis_data"],
                    "value": [{"value": 1}],
                },
                {
                    "ref": "step-output://right/data",
                    "argument_name": "data",
                    "output_name": "data",
                    "source_step_id": "right",
                    "semantic_roles": ["analysis_data"],
                    "value": [{"value": 2}],
                },
            ],
        )

        self.assertNotIn("records", result.arguments)
        self.assertIn("is unbound", "; ".join(result.errors))

    def test_router_repairs_invalid_tool_binding_before_other_fallbacks(self):
        engine = ReportEngine(llm=object())
        router = _RepairingRouter()
        engine.router_agent = router
        runtime = EngineRuntimeContext(
            mcp_tools=[
                MCPToolDefinition(
                    name="metadata_by_dataset",
                    input_schema={
                        "type": "object",
                        "properties": {"dataset_id": {"type": "string"}},
                        "required": ["dataset_id"],
                    },
                )
            ]
        )
        update = engine._data_route(
            {
                "step": {
                    "step_id": "interpret",
                    "description": "Interpret the source evidence.",
                    "operation": {
                        "kind": "interpret",
                        "capability": "semantic.interpret",
                        "execution_mode": "auto",
                    },
                    "outputs": [{"name": "findings", "shape": "table"}],
                },
                "spec": ExecutionSpec(
                    intent="report",
                    objective="Interpret the evidence",
                ),
                "corpus_package": DataCorpusPackage(),
                "runtime": runtime,
                "resolved_inputs": [
                    {
                        "ref": "step-output://source/records",
                        "argument_name": "records",
                        "output_name": "records",
                        "source_step_id": "source",
                        "semantic_roles": ["source_content"],
                        "value": [{"text": "Evidence"}],
                    }
                ],
            }
        )

        self.assertEqual(update["route"]["route"], SEMANTIC_ANALYSIS_ROUTE)
        self.assertEqual(len(router.calls), 2)
        self.assertTrue(router.calls[1]["feedback"])
        self.assertEqual(
            router.calls[1]["excluded"],
            ["metadata_by_dataset"],
        )

    def test_semantic_agent_batches_every_evidence_record_without_sampling(self):
        agent = SemanticAnalysisAgent(None, max_batch_characters=4_000)
        records = [
            {"text": f"record-{index}-" + ("x" * 2_000)}
            for index in range(3)
        ]

        batches = agent._evidence_batches(
            [
                {
                    "ref": "step-output://source/records",
                    "artifact_ref": "artifact://source",
                    "semantic_roles": ["source_content"],
                    "value": records,
                }
            ]
        )

        observed = [
            item["value"]["text"]
            for batch in batches
            for item in batch
        ]
        self.assertEqual(observed, [item["text"] for item in records])
        self.assertGreater(len(batches), 1)

    def test_report_contract_validator_rejects_dependency_cycle(self):
        errors = ReportContractValidator().validate_plan(
            {
                "steps": [
                    {
                        "step_id": "left",
                        "depends_on": ["right"],
                        "inputs": [
                            {"ref": "step-output://right/value"}
                        ],
                        "operation": {"execution_mode": "auto"},
                        "outputs": [{"name": "value"}],
                    },
                    {
                        "step_id": "right",
                        "depends_on": ["left"],
                        "inputs": [
                            {"ref": "step-output://left/value"}
                        ],
                        "operation": {"execution_mode": "auto"},
                        "outputs": [{"name": "value"}],
                    },
                ]
            }
        )

        self.assertIn("dependency cycle", "; ".join(errors))

    def test_report_graph_uses_report_langsmith_run_name(self):
        engine = object.__new__(ReportEngine)
        engine.llm = None
        engine.max_data_concurrency = 1
        engine.max_chart_concurrency = 1
        engine.format_registry = ReportFormatRegistry()
        engine.default_locale = "en"
        engine._graph = Mock()
        engine._graph.invoke.return_value = {"final_result": "report"}

        engine.run(
            EngineInput(
                query=UserQuery(text="Create a report"),
                spec=ExecutionSpec(intent="report", objective="Create a report"),
                corpus_package=DataCorpusPackage(),
                runtime=EngineRuntimeContext(),
            )
        )

        config = engine._graph.invoke.call_args.kwargs["config"]
        self.assertEqual(config["run_name"], "report")

    def test_router_prefers_builtin_spreadsheet_materializer(self):
        source = r"G:\uploads\scores.xls"
        route = RouterAgent(None).run(
            {
                "step_id": "load-scores",
                "description": "Materialize the spreadsheet rows.",
                "operation": {"kind": "materialize_source"},
            },
            [
                {
                    "tool_name": "materialize_spreadsheet",
                    "description": "Read XLS and XLSX workbooks.",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                    "capability_names": ["materialize_source"],
                }
            ],
            [source],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["tool_name"], "materialize_spreadsheet")
        self.assertEqual(route["arguments"], {"path": source})

    def test_generated_materializer_cannot_mask_read_error_as_empty(self):
        source_code = """
def load_rows(path: str) -> list[dict]:
    try:
        return read_workbook(path)
    except Exception:
        return []
"""
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "load_rows",
                "source_code": source_code,
                "parameters_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {"path": "scores.xls"},
            {"operation": {"kind": "materialize_source"}},
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("must not catch read or parser errors", errors[0])

    def test_pipeline_preserves_report_metadata_without_evidence(self):
        engine = _FakeEngine()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=object(),
            spec_builder=object(),
            spec_confirmation=object(),
            engine_registry=_FakeRegistry(engine),
            include_evidence=False,
        )
        query = UserQuery(text="Create a report")
        spec = ExecutionSpec(
            intent="report",
            objective=query.text,
            confirmed=True,
            engine_hint="report",
            constraints={"output_format": "html"},
        )
        prepared = PreparedExecution(
            query=query,
            intent="report",
            spec=spec,
        )

        response = pipeline.execute_confirmed_spec(prepared, spec)

        self.assertEqual(response.metadata["engine_name"], "report")
        self.assertEqual(response.metadata["report_format"], "html")
        self.assertEqual(
            response.metadata["rendered_reports"][0]["format"],
            "html",
        )

    def test_pipeline_uses_engine_artifact_as_final_response_with_evidence(self):
        engine = _FakeEngine()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=object(),
            spec_builder=object(),
            spec_confirmation=object(),
            engine_registry=_FakeRegistry(engine),
        )
        query = UserQuery(text="Create a report")
        spec = ExecutionSpec(
            intent="report",
            objective=query.text,
            confirmed=True,
            engine_hint="report",
        )
        prepared = PreparedExecution(
            query=query,
            intent="report",
            spec=spec,
        )

        response = pipeline.execute_confirmed_spec(prepared, spec)

        self.assertEqual(response.answer, "<html><body>Report</body></html>")
        self.assertEqual(response.evidence, EvidenceBundle(sources=["sales.csv"]))
        self.assertEqual(response.metadata["engine_name"], "report")
        self.assertEqual(response.metadata["report_format"], "html")

    def test_request_sandbox_executor_calls_generated_function_with_staged_path(self):
        session = _FakeSandboxSession()
        executor = RequestSandboxExecutor(session, run_artifact=object())
        interface = InterfaceDefinition(
            name="profile_sales",
            implementation_ref=(
                "def profile_sales(path: str):\n"
                "    return {'path': path, 'count': 3}\n"
            ),
            metadata={
                "source_code": (
                    "def profile_sales(path: str):\n"
                    "    return {'path': path, 'count': 3}\n"
                )
            },
        )

        result = executor.run(
            interface,
            {"path": "C:\\uploads\\sales.csv"},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result, {"count": 3})
        self.assertIn("/workspace/input/sales.csv", session.code)
        self.assertIn("result = __report_tool", session.code)

    def test_request_sandbox_executor_reprovisions_failed_sandbox_once(self):
        session = _ReprovisioningSandboxSession()
        executor = RequestSandboxExecutor(session, run_artifact=object())
        interface = InterfaceDefinition(
            name="profile_sales",
            implementation_ref="def profile_sales():\n    return {'count': 3}\n",
            metadata={
                "source_code": "def profile_sales():\n    return {'count': 3}\n"
            },
        )

        result = executor.run(interface, {})

        self.assertEqual(result.status, "completed")
        self.assertEqual(session.calls, 2)
        self.assertEqual(session.reprovision_calls, 1)

    def test_plan_agent_normalizes_scalar_step_contract_fields(self):
        spec = ExecutionSpec(intent="report", objective="Create a report")
        plan = PlanAgent(None)._normalize_plan(
            {
                "revision": "v1",
                "steps": [
                    {
                        "step_id": "overview",
                        "inputs": "corpus://sales",
                        "depends_on": "load-data",
                        "required_data": {
                            "tables": "sales",
                            "columns": "revenue",
                        },
                        "operation": "inspect",
                        "outputs": "sales-profile",
                    }
                ],
            },
            spec,
            DataCorpusPackage(),
            None,
        )

        step = plan["steps"][0]
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(step["inputs"][0]["ref"], "corpus://sales")
        self.assertEqual(step["depends_on"], [])
        self.assertEqual(step["required_data"]["tables"], ["sales"])
        self.assertEqual(step["required_data"]["columns"], ["revenue"])
        self.assertEqual(
            step["operation"],
            {
                "kind": "inspect",
                "capability": "inspect",
                "execution_mode": "auto",
                "execution_class": "auto",
                "parameters": {},
            },
        )
        self.assertEqual(step["outputs"][0]["name"], "sales-profile")

    def test_plan_agent_ignores_steps_owned_by_downstream_report_components(self):
        spec = ExecutionSpec(intent="report", objective="Create a report")
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "read-data",
                        "operation": {"kind": "read_csv"},
                    },
                    {
                        "step_id": "describe-data",
                        "operation": {"kind": "describe_dataframe"},
                    },
                    {
                        "step_id": "render-report",
                        "operation": {"kind": "generate_report"},
                    },
                    {
                        "step_id": "prepare_report_payload",
                        "description": (
                            "Prepare CSV metadata for downstream reporting."
                        ),
                        "operation": {"kind": "format_metadata"},
                        "outputs": [{"name": "report_payload"}],
                    },
                    {
                        "step_id": "build-report-payload",
                        "operation": {"kind": "construct_report_payload"},
                    },
                    {
                        "step_id": "compile-report-payload",
                        "operation": {"kind": "compile_report_payload"},
                    },
                ]
            },
            spec,
            DataCorpusPackage(),
            None,
        )

        self.assertEqual(
            [step["step_id"] for step in plan["steps"]],
            ["read-data"],
        )
        self.assertIn("describe-data", plan["warnings"][0])
        self.assertIn("render-report", plan["warnings"][0])
        self.assertIn("prepare_report_payload", plan["warnings"][0])
        self.assertIn("build-report-payload", plan["warnings"][0])
        self.assertIn("compile-report-payload", plan["warnings"][0])

    def test_plan_agent_binds_dependency_to_named_upstream_output(self):
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "load-data",
                        "operation": {"kind": "load"},
                        "outputs": [{"name": "loaded-rows"}],
                    },
                    {
                        "step_id": "calculate",
                        "depends_on": ["load-data"],
                        "inputs": [{"name": "rows", "required": True}],
                        "operation": {"kind": "calculate"},
                    },
                ]
            },
            ExecutionSpec(intent="report", objective="Create a report"),
            DataCorpusPackage(),
            None,
        )

        child = plan["steps"][1]
        self.assertEqual(
            child["inputs"][0]["ref"],
            "step-output://load-data/loaded-rows",
        )
        self.assertEqual(child["inputs"][0]["name"], "rows")

    def test_plan_agent_canonicalizes_output_name_reference(self):
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "load",
                        "outputs": [{"name": "rows"}],
                    },
                    {
                        "step_id": "calculate",
                        "depends_on": ["load"],
                        "inputs": [{"ref": "rows", "required": True}],
                    },
                ]
            },
            ExecutionSpec(intent="report", objective="Create a report"),
            DataCorpusPackage(),
            None,
        )

        inputs = plan["steps"][1]["inputs"]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["ref"], "step-output://load/rows")

    def test_plan_agent_rewrites_local_registration_as_generic_materialization(self):
        for filename in ("records.csv", "workbook.xlsx", "document.pdf", "notes.txt"):
            with self.subTest(filename=filename):
                source = f"G:\\uploads\\{filename}"
                spec = ExecutionSpec(
                    intent="report",
                    objective="Create a report about this source",
                    data_requirements=[source],
                )

                plan = PlanAgent(None)._normalize_plan(
                    {
                        "steps": [
                            {
                                "step_id": "register-source",
                                "description": "Upload and register the local source.",
                                "depends_on": [],
                                "inputs": [{"ref": "source_path"}],
                                "operation": {"kind": "upload_source"},
                                "outputs": [
                                    {"name": "source-records", "shape": "table"}
                                ],
                            }
                        ]
                    },
                    spec,
                    DataCorpusPackage(sources=[source]),
                    None,
                )

                step = plan["steps"][0]
                self.assertEqual(
                    step["operation"]["kind"],
                    "read_source_content",
                )
                self.assertEqual(
                    step["operation"]["parameters"]["sources"],
                    [source],
                )
                self.assertEqual(
                    step["operation"]["parameters"]["source_extensions"],
                    [Path(source).suffix.lower()],
                )
                self.assertIn("source-specific structure", step["description"])

    def test_router_canonicalizes_model_path_to_allowed_source(self):
        source = "G:\\repo\\.uploads\\hyperactivated.csv"
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "scan_csv",
                "arguments": {"path": "G:\\\\repo\\\\.uploads\\\\hyperactivated.csv"},
            },
            {"description": "Read CSV"},
            [
                {
                    "tool_name": "scan_csv",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            [source],
        )

        self.assertEqual(route["arguments"]["path"], source)

    def test_router_uses_trusted_csv_tool_for_model_generated_load_route(self):
        source = "G:\\repo\\.uploads\\hyperactivated.csv"
        route = RouterAgent(None)._normalize_route(
            {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
            },
            {
                "description": "Load the CSV file",
                "operation": {"kind": "load"},
            },
            [
                {
                    "tool_name": "scan_csv",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            [source],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["tool_name"], "scan_csv")
        self.assertEqual(route["arguments"]["path"], source)

    def test_router_uses_trusted_pdf_tool_for_document_steps(self):
        source = "G:\\repo\\.uploads\\document.pdf"
        route = RouterAgent(None)._normalize_route(
            {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
            },
            {
                "description": "Extract text from the PDF",
                "operation": {"kind": "extract_text"},
            },
            [
                {
                    "tool_name": "extract_pdf_text",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            [source],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["tool_name"], "extract_pdf_text")
        self.assertEqual(route["arguments"]["path"], source)

    def test_generated_source_repairs_double_escaped_newlines(self):
        source = "def load_pdf(path: str):\\n" '    return [{\\"path\\": path}]\\n'

        repaired = _normalize_generated_source(source)

        compile(repaired, "<test-generated-source>", "exec")
        self.assertIn("\n    return", repaired)
        self.assertNotIn("\\n", repaired)

    def test_generated_source_repairs_mixed_real_and_escaped_newlines(self):
        source = (
            "import json\\n\\ndef load_rows(path: str):\\n"
            "    \"\"\"Load rows.\n\n    The path is read-only.\n    \"\"\"\\n"
            "    with open(path, encoding=\"utf-8\") as stream:\\n"
            "        return json.load(stream)\\n"
        )

        repaired = _normalize_generated_source(source)

        compile(repaired, "<test-generated-source>", "exec")
        self.assertIn("def load_rows", repaired)

    def test_code_agent_canonicalizes_and_fills_source_path_arguments(self):
        source = "G:\\repo\\.uploads\\document.pdf"

        arguments = CodeAgent._normalize_execution_arguments(
            {"pdf_path": "G:\\\\repo\\\\.uploads\\\\document.pdf"},
            {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string"},
                    "backup_path": {"type": "string"},
                },
            },
            [source],
        )

        self.assertEqual(arguments["pdf_path"], source)
        self.assertEqual(arguments["backup_path"], source)

    def test_code_agent_replaces_placeholder_source_path(self):
        source = "G:\\repo\\.uploads\\document.pdf"

        arguments = CodeAgent._normalize_execution_arguments(
            {"source_path": "<source_path>"},
            {
                "type": "object",
                "properties": {"source_path": {"type": "string"}},
                "required": ["source_path"],
            },
            [source],
        )

        self.assertEqual(arguments["source_path"], source)

    def test_code_agent_binds_multiple_generic_source_paths(self):
        sources = [
            "G:\\repo\\.uploads\\records.csv",
            "G:\\repo\\.uploads\\notes.txt",
        ]

        arguments = CodeAgent._normalize_execution_arguments(
            {"paths": ["<path>"], "output_path": "result.json"},
            {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "output_path": {"type": "string"},
                },
                "required": ["paths"],
            },
            sources,
        )

        self.assertEqual(arguments["paths"], sources)
        self.assertEqual(arguments["output_path"], "result.json")

    def test_code_agent_fails_closed_when_generation_is_unavailable(self):
        code_spec = CodeAgent(None).run(
            {"step_id": "analyze"},
            {"sources": ["source.csv"]},
        )

        self.assertEqual(code_spec["source_code"], "")
        self.assertIn("generation_error", code_spec)
        self.assertNotIn("return []", str(code_spec))

    def test_generated_source_must_define_declared_tool_name(self):
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "inspect_pdf",
                "source_code": "def run(path: str):\n    return []\n",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {"path": "document.pdf"},
        )

        self.assertEqual(
            errors,
            [
                "Generated source_code must define a top-level function named "
                "inspect_pdf."
            ],
        )

    def test_source_context_does_not_overwrite_content_analysis(self):
        decision = DataScienceProcessor._normalize_trusted_analysis(
            {
                "status": "success",
                "analysis_summary": "The document explains a three-stage design process.",
                "aggregated_data": {"design_stages": 3},
                "warnings": ["One page was truncated."],
            },
            {"tool_name": "extract_pdf_text"},
            [
                {
                    "page_number": 1,
                    "text": "abc",
                    "character_count": 3,
                    "truncated": False,
                }
            ],
            {"page_count": 1, "character_count": 3},
            "memory://report/pdf",
        )

        self.assertEqual(
            decision["analysis_summary"],
            "The document explains a three-stage design process.",
        )
        self.assertEqual(decision["aggregated_data"]["design_stages"], 3)
        self.assertEqual(
            decision["aggregated_data"]["source_context"],
            {
                "record_count": 1,
                "page_count": 1,
                "character_count": 3,
                "truncated_record_count": 0,
            },
        )
        self.assertEqual(decision["warnings"], [])

    def test_analysis_sample_is_stratified_and_bounded(self):
        rows = [{"position": index, "text": "x" * 20} for index in range(20)]

        sample = DataScienceProcessor._analysis_sample(
            rows,
            max_rows=4,
            max_string_chars=5,
        )

        self.assertEqual(
            [item["position"] for item in sample],
            [0, 6, 13, 19],
        )
        self.assertTrue(
            all(item["text"] == "xxxxx... [sample truncated]" for item in sample)
        )

    def test_analysis_sample_spans_a_long_text_field(self):
        text = "HEAD-" + ("x" * 5890) + "-TAIL"

        sample = DataScienceProcessor._analysis_sample(
            [{"text": text}],
            max_string_chars=600,
        )

        sampled_text = sample[0]["text"]
        self.assertIn("HEAD-", sampled_text)
        self.assertIn("-TAIL", sampled_text)
        self.assertEqual(sampled_text.count("[sample gap]"), 5)

    def test_overview_metrics_respect_configured_card_limit(self):
        aggregated = DataScienceProcessor._overview_aggregated_data(
            {"objective_score": 92, "source_context": {"record_count": 2}},
            [
                {"section": "A", "text": "alpha beta"},
                {"section": "B", "text": "gamma"},
            ],
            max_metrics=4,
        )

        display_values = [
            (name, value)
            for name, value in aggregated.items()
            if name != "source_context"
        ]
        self.assertEqual(len(display_values), 4)
        self.assertEqual(display_values[0], ("objective_score", 92))
        self.assertEqual(aggregated["record_count"], 2)
        self.assertEqual(aggregated["field_count"], 2)

    def test_report_content_normalizes_summary_sentence_arrays(self):
        content = DataScienceProcessor._normalize_report_content(
            {
                "analysis_summary": "Fallback.",
                "report_content": {
                    "executive_summary": [
                        "First sentence.",
                        "Second sentence.",
                    ]
                },
            }
        )

        self.assertEqual(
            content["executive_summary"],
            "First sentence. Second sentence.",
        )

    def test_fractional_coverage_metric_is_presented_as_percent(self):
        aggregated = DataScienceProcessor._overview_aggregated_data(
            {"concept_coverage": 0.75},
            [{"text": "content"}],
        )

        self.assertEqual(aggregated["concept_coverage_percent"], 75)

    def test_chart_dataset_prefers_agent_content_comparison(self):
        chart = DataScienceProcessor._chart_dataset(
            {
                "chart_data": {
                    "title": "Concept emphasis",
                    "coverage": "12 sampled document units",
                    "rows": [
                        {"category": "Modularity", "value": 7},
                        {"category": "Cohesion", "value": 5},
                    ],
                }
            },
            [{"text": "fallback text"}],
        )

        self.assertEqual(chart["title"], "Concept emphasis")
        self.assertEqual(
            chart["rows"],
            [
                {"category": "Modularity", "value": 7},
                {"category": "Cohesion", "value": 5},
            ],
        )

    def test_chart_dataset_does_not_invent_text_frequency_chart(self):
        chart = DataScienceProcessor._chart_dataset(
            {},
            [
                {
                    "text": (
                        "modularity cohesion modularity abstraction "
                        "cohesion modularity"
                    )
                }
            ],
        )

        self.assertFalse(chart["render"])
        self.assertFalse(chart["rows"])
        self.assertIn("did not identify a chart", chart["reason"])

    def test_step_output_registry_persists_stages_and_binds_generated_path(self):
        with tempfile.TemporaryDirectory() as directory:
            run_artifact = RunArtifactSession.create(
                run_id="00000000-0000-0000-0000-000000000001",
                root=Path(directory) / "run",
                query=UserQuery(text="test"),
            )
            sandbox = _RuntimeSandbox()
            runtime = EngineRuntimeContext(
                run_artifact=run_artifact,
                sandbox=sandbox,
            )
            registry = _StepOutputRegistry()
            descriptors, warnings = registry.register(
                {
                    "step_id": "load-data",
                    "outputs": [{"name": "loaded-rows", "shape": "table"}],
                },
                [{"value": 2}],
                runtime,
            )
            resolver = _StepInputResolver()
            resolved, missing = resolver.resolve(
                {
                    "step_id": "calculate",
                    "depends_on": ["load-data"],
                    "inputs": [
                        {
                            "name": "rows",
                            "ref": "step-output://load-data/loaded-rows",
                            "required": True,
                        }
                    ],
                },
                registry,
            )
            arguments = resolver.merge_arguments(
                {},
                {
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array"},
                        "rows_path": {"type": "string"},
                    },
                },
                resolved,
                sandbox=True,
            )

            persisted = (
                Path(directory) / "run" / "data" / "load-data" / "loaded-rows.json"
            )
            self.assertEqual(warnings, [])
            self.assertEqual(missing, [])
            self.assertTrue(persisted.exists())
            self.assertEqual(
                descriptors[0]["artifact_ref"].split("/")[-1], "loaded-rows.json"
            )
            self.assertIn(
                "intermediate/load-data/loaded-rows.json", sandbox.sandbox.files
            )
            self.assertEqual(
                arguments["rows_path"],
                "/workspace/intermediate/load-data/loaded-rows.json",
            )
            self.assertEqual(arguments["rows"], [{"value": 2}])
            self.assertEqual(resolved[0]["json_type"], "array")
            self.assertEqual(
                resolved[0]["schema"]["fields"][0]["name"],
                "value",
            )

    def test_scheduler_skips_required_downstream_after_dependency_failure(self):
        engine = object.__new__(ReportEngine)
        runtime = EngineRuntimeContext()
        state = {
            "runtime": runtime,
            "plan": {
                "steps": [
                    {"step_id": "load", "depends_on": [], "inputs": []},
                    {
                        "step_id": "transform",
                        "depends_on": ["load"],
                        "inputs": [
                            {
                                "ref": "step-output://load/rows",
                                "required": True,
                            }
                        ],
                    },
                ]
            },
            "completed_step_ids": ["load"],
            "data_step_results": [{"step_id": "load", "status": "failed"}],
        }

        update = engine._graph_schedule_data(state)

        self.assertEqual(update["ready_steps"], [])
        self.assertEqual(update["completed_step_ids"], ["transform"])
        self.assertEqual(update["data_step_results"][0]["status"], "skipped")
        self.assertIn("load", update["data_step_results"][0]["warnings"][0])

    def test_scheduler_allows_optional_failed_dependency(self):
        engine = object.__new__(ReportEngine)
        state = {
            "runtime": EngineRuntimeContext(),
            "plan": {
                "steps": [
                    {"step_id": "load", "depends_on": [], "inputs": []},
                    {
                        "step_id": "optional-transform",
                        "depends_on": ["load"],
                        "inputs": [
                            {
                                "ref": "step-output://load/rows",
                                "required": False,
                            }
                        ],
                    },
                ]
            },
            "completed_step_ids": ["load"],
            "data_step_results": [{"step_id": "load", "status": "failed"}],
        }

        update = engine._graph_schedule_data(state)

        self.assertEqual(
            [step["step_id"] for step in update["ready_steps"]],
            ["optional-transform"],
        )
        self.assertEqual(update["completed_step_ids"], [])

    def test_execution_plan_keeps_only_template_consumers_and_dependencies(self):
        plan = {
            "steps": [
                {"step_id": "load", "depends_on": [], "inputs": []},
                {
                    "step_id": "derive",
                    "depends_on": ["load"],
                    "inputs": [{"ref": "step-output://load/rows"}],
                },
                {
                    "step_id": "unused-summary",
                    "depends_on": ["derive"],
                    "inputs": [{"ref": "step-output://derive/evidence"}],
                },
            ]
        }
        instance = {
            "bindings": [
                {
                    "status": "resolved",
                    "plan_output_refs": ["step-output://derive/evidence"],
                }
            ]
        }

        execution_plan = ReportEngine._plan_for_template(plan, instance)

        self.assertEqual(
            [step["step_id"] for step in execution_plan["steps"]],
            ["load", "derive"],
        )
        self.assertIn("unused-summary", execution_plan["warnings"][0])

    def test_force_code_agent_bypasses_router_and_existing_tools(self):
        engine = ReportEngine(llm=object(), force_code_agent=True)
        engine.router_agent = _ExplodingRouterAgent()

        update = engine._data_route(
            {
                "step": {
                    "step_id": "inspect",
                    "description": "Inspect the input source.",
                },
                "spec": ExecutionSpec(
                    intent="report",
                    objective="Inspect the source",
                ),
                "corpus_package": DataCorpusPackage(sources=["source.csv"]),
                "runtime": EngineRuntimeContext(),
                "resolved_inputs": [],
            }
        )

        self.assertEqual(update["route"]["route"], "generate_tool")
        self.assertIsNone(update["route"]["tool_name"])
        self.assertIn("force_code_agent", update["route"]["reason"])

    def test_source_content_dependency_is_routed_to_semantic_execution(self):
        engine = ReportEngine(llm=object())
        router = _RecordingSemanticRouter()
        engine.router_agent = router
        engine.semantic_analysis_agent = _SemanticExecutionAgent()
        runtime = EngineRuntimeContext()
        registry = _StepOutputRegistry()
        source_rows = [
            {
                "document_id": "document-a",
                "record_kind": "indexed_chunk",
                "text": "Monthly volume increased while incidents declined.",
            }
        ]
        registry.register(
            {
                "step_id": "materialize-source",
                "outputs": [
                    {
                        "name": "document_records",
                        "shape": "table",
                        "semantic_roles": ["primary_source", "source_content"],
                    }
                ],
            },
            source_rows,
            runtime,
        )
        state = {
            "step": {
                "step_id": "summarize",
                "description": "Summarize the materialized source evidence.",
                "depends_on": ["materialize-source"],
                "inputs": [
                    {
                        "name": "document_records",
                        "ref": (
                            "step-output://materialize-source/document_records"
                        ),
                        "required": True,
                    }
                ],
                "outputs": [{"name": "summary", "shape": "table"}],
            },
            "spec": ExecutionSpec(intent="report", objective="Summarize evidence"),
            "corpus_package": DataCorpusPackage(),
            "runtime": runtime,
            "output_registry": registry,
        }
        state.update(engine._data_resolve_inputs(state))

        route_update = engine._data_route(state)
        state.update(route_update)
        execution_update = engine._data_execute_semantic(state)

        self.assertEqual(
            route_update["route"]["route"],
            SEMANTIC_ANALYSIS_ROUTE,
        )
        self.assertEqual(router.input_contracts[0]["json_type"], "array")
        self.assertEqual(
            state["resolved_inputs"][0]["semantic_roles"],
            ["primary_source", "source_content"],
        )
        self.assertEqual(
            execution_update["execution_result"]["raw_result"][0]["finding"],
            "Monthly volume increased while incidents declined.",
        )
        self.assertEqual(
            execution_update["execution_result"]["metadata"]["provider"],
            "semantic_analysis_agent",
        )

    def test_plain_tabular_dependency_still_uses_router_or_code_agent(self):
        engine = ReportEngine(llm=object())
        engine.router_agent = _GeneratedRouteAgent()
        runtime = EngineRuntimeContext()
        registry = _StepOutputRegistry()
        registry.register(
            {
                "step_id": "load-table",
                "outputs": [
                    {
                        "name": "rows",
                        "shape": "table",
                        "semantic_roles": ["analysis_data"],
                    }
                ],
            },
            [{"value": 2}, {"value": 3}],
            runtime,
        )
        state = {
            "step": {
                "step_id": "sum",
                "description": "Sum the values.",
                "depends_on": ["load-table"],
                "inputs": [
                    {
                        "name": "rows",
                        "ref": "step-output://load-table/rows",
                        "required": True,
                    }
                ],
                "outputs": [{"name": "totals", "shape": "table"}],
            },
            "spec": ExecutionSpec(intent="report", objective="Calculate a total"),
            "corpus_package": DataCorpusPackage(),
            "runtime": runtime,
            "output_registry": registry,
        }
        state.update(engine._data_resolve_inputs(state))

        update = engine._data_route(state)

        self.assertEqual(update["route"]["route"], "generate_tool")

    def test_method_binding_mismatch_uses_local_contract_when_data_is_ready(self):
        route = ReportEngine._local_route_after_method_mismatch(
            {
                "step_id": "reshape",
                "operation": {
                    "kind": "reshape",
                    "execution_mode": "method_hub",
                    "execution_class": "deterministic_transform",
                },
                "outputs": [
                    {
                        "name": "rows",
                        "semantic_roles": ["analysis_data"],
                    }
                ],
            },
            [
                {
                    "ref": "step-output://load/rows",
                    "value": [{"value": 1}],
                }
            ],
            ["Required Method Hub argument 'file_name' is unbound."],
            {"route": "unsupported", "reason": "No compatible tool."},
        )

        self.assertEqual(route["route"], "generate_tool")
        self.assertIn("already materialized", route["reason"])

    def test_method_binding_mismatch_does_not_fake_external_retrieval(self):
        route = ReportEngine._local_route_after_method_mismatch(
            {
                "step_id": "retrieve",
                "operation": {"execution_mode": "method_hub"},
            },
            [{"ref": "corpus://org/document", "value": None}],
            ["No compatible tool."],
            {"route": "unsupported", "reason": "No compatible tool."},
        )

        self.assertEqual(route["route"], "unsupported")

    def test_router_preflight_excludes_schema_incompatible_tools(self):
        engine = ReportEngine(llm=object())
        resolved = [
            {
                "ref": "step-output://source/records",
                "argument_name": "records",
                "value": [{"text": "Evidence"}],
                "json_type": "array",
                "structure": {
                    "type": "array",
                    "item": {"type": "object"},
                },
            }
        ]
        exclusions = engine._method_tool_preflight_exclusions(
            [
                {
                    "tool_name": "retrieve_by_file",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"file_name": {"type": "string"}},
                        "required": ["file_name"],
                    },
                },
                {
                    "tool_name": "numeric_scores",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "values": {
                                "type": "array",
                                "items": {"type": "number"},
                            }
                        },
                        "required": ["values"],
                    },
                },
                {
                    "tool_name": "process_records",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "records": {
                                "type": "array",
                                "items": {"type": "object"},
                            }
                        },
                        "required": ["records"],
                    },
                },
            ],
            {
                "operation": {
                    "kind": "transform",
                    "capability": "transform",
                    "parameters": {},
                }
            },
            resolved,
        )

        self.assertEqual(
            exclusions,
            ["retrieve_by_file", "numeric_scores"],
        )

    def test_report_engine_defaults_to_four_code_generation_attempts(self):
        engine = ReportEngine(llm=object())

        self.assertEqual(engine.max_generation_attempts, 4)

    def test_generated_step_receives_materialized_upstream_rows(self):
        engine = ReportEngine(llm=object())
        engine.router_agent = _GeneratedRouteAgent()
        engine.code_agent = _SumCodeAgent()
        sandbox = _ExecutingSandbox()
        runtime = EngineRuntimeContext(sandbox_executor=sandbox)
        registry = _StepOutputRegistry()
        registry.register(
            {
                "step_id": "load",
                "outputs": [{"name": "rows", "shape": "table"}],
            },
            [{"value": 2}, {"value": 3}],
            runtime,
        )

        state = engine._data_step_graph.invoke(
            {
                "step": {
                    "step_id": "sum",
                    "description": "Sum the upstream rows.",
                    "depends_on": ["load"],
                    "inputs": [
                        {
                            "name": "rows",
                            "ref": "step-output://load/rows",
                            "required": True,
                        }
                    ],
                    "outputs": [{"name": "totals", "shape": "table"}],
                },
                "spec": ExecutionSpec(
                    intent="report",
                    objective="Calculate a total",
                ),
                "corpus_package": DataCorpusPackage(),
                "runtime": runtime,
                "output_registry": registry,
                "template_requirements": [],
                "upstream_step_results": [{"step_id": "load", "status": "completed"}],
                "attempt": 0,
            },
            config={"recursion_limit": 30},
        )

        result = state["data_step_result"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["step_result_artifact"]["sample"], [{"total": 5}])
        self.assertEqual(result["lineage"]["upstream_step_refs"], ["load"])
        self.assertEqual(sandbox.validate_calls, 1)
        self.assertEqual(sandbox.run_calls, 0)
        self.assertNotIn("registered_method_name", state["execution_result"])

    def test_generated_runtime_failure_retries_code_agent_then_reuses_result(self):
        engine = ReportEngine(llm=object(), max_generation_attempts=2)
        engine.router_agent = _GeneratedRouteAgent()
        code_agent = _ConstantCodeAgent()
        engine.code_agent = code_agent
        sandbox = _RetryingSandbox()

        state = engine._data_step_graph.invoke(
            {
                "step": {
                    "step_id": "sum",
                    "description": "Produce a total.",
                    "depends_on": [],
                    "inputs": [],
                    "outputs": [{"name": "totals", "shape": "table"}],
                },
                "spec": ExecutionSpec(
                    intent="report",
                    objective="Calculate a total",
                ),
                "corpus_package": DataCorpusPackage(),
                "runtime": EngineRuntimeContext(sandbox_executor=sandbox),
                "output_registry": _StepOutputRegistry(),
                "template_requirements": [],
                "upstream_step_results": [],
                "attempt": 0,
            },
            config={"recursion_limit": 30},
        )

        self.assertEqual(state["data_step_result"]["status"], "completed")
        self.assertEqual(code_agent.calls, 2)
        self.assertIn(
            "Synthetic runtime failure",
            code_agent.feedback[1]["error_logs"],
        )
        self.assertEqual(sandbox.validate_calls, 2)
        self.assertEqual(sandbox.run_calls, 0)

    def test_sandbox_failure_cannot_be_overridden_by_validator_pass(self):
        engine = ReportEngine(llm=object(), max_generation_attempts=1)
        engine.router_agent = _GeneratedRouteAgent()
        engine.code_agent = _ConstantCodeAgent()
        engine.validator_agent = _AlwaysPassValidator()
        sandbox = _RetryingSandbox()

        state = engine._data_step_graph.invoke(
            {
                "step": {
                    "step_id": "sum",
                    "description": "Produce a total.",
                    "depends_on": [],
                    "inputs": [],
                    "outputs": [{"name": "totals", "shape": "table"}],
                },
                "spec": ExecutionSpec(
                    intent="report",
                    objective="Calculate a total",
                ),
                "corpus_package": DataCorpusPackage(),
                "runtime": EngineRuntimeContext(sandbox_executor=sandbox),
                "output_registry": _StepOutputRegistry(),
                "template_requirements": [],
                "upstream_step_results": [],
                "attempt": 0,
            },
            config={"recursion_limit": 30},
        )

        self.assertEqual(state["data_step_result"]["status"], "failed")
        self.assertIn(
            "Synthetic runtime failure",
            state["data_step_result"]["warnings"][0],
        )
        self.assertEqual(sandbox.validate_calls, 1)
        self.assertEqual(sandbox.run_calls, 0)


if __name__ == "__main__":
    unittest.main()
