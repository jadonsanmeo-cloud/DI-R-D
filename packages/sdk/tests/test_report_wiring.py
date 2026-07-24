import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
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
    RouterAgent,
    _StepInputResolver,
    _StepOutputRegistry,
    _normalize_generated_source,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.report_sandbox_executor import (
    RequestSandboxExecutor,
)
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession
from data_intelligence_sdk.sandbox.executor import SandboxRunResult


class _FakeEngine:
    name = "report"

    def run(self, spec, corpus_package, runtime, user_context=None):
        del spec, corpus_package, runtime, user_context
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


class _WritableSandbox:
    def __init__(self):
        self.files = {}

    def write(self, path, content):
        self.files[path] = content


class _RuntimeSandbox:
    def __init__(self):
        self.sandbox = _WritableSandbox()


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
    def test_report_graph_uses_report_langsmith_run_name(self):
        engine = object.__new__(ReportEngine)
        engine.llm = None
        engine.max_data_concurrency = 1
        engine.max_chart_concurrency = 1
        engine._graph = Mock()
        engine._graph.invoke.return_value = {"final_result": "report"}

        engine.run(
            ExecutionSpec(intent="report", objective="Create a report"),
            DataCorpusPackage(),
            EngineRuntimeContext(),
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
        corpus = DataCorpusPackage(sources=["sales.csv"])
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
            corpus_package=corpus,
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
        corpus = DataCorpusPackage(sources=["sales.csv"])
        spec = ExecutionSpec(
            intent="report",
            objective=query.text,
            confirmed=True,
            engine_hint="report",
        )
        prepared = PreparedExecution(
            query=query,
            intent="report",
            corpus_package=corpus,
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
        self.assertEqual(step["operation"], {"kind": "inspect"})
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
                                "operation": {"kind": "inspect_data"},
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

    def test_overview_metrics_are_dynamic_and_always_fill_four_cards(self):
        aggregated = DataScienceProcessor._overview_aggregated_data(
            {"objective_score": 92, "source_context": {"record_count": 2}},
            [
                {"section": "A", "text": "alpha beta"},
                {"section": "B", "text": "gamma"},
            ],
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

    def test_chart_dataset_has_general_text_fallback(self):
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

        self.assertGreaterEqual(len(chart["rows"]), 2)
        self.assertEqual(chart["rows"][0]["category"], "modularity")
        self.assertEqual(chart["rows"][0]["value"], 3)

    def test_step_output_registry_persists_stages_and_binds_generated_path(self):
        with tempfile.TemporaryDirectory() as directory:
            run_artifact = RunArtifactSession.create(
                run_id="00000000-0000-0000-0000-000000000001",
                root=Path(directory) / "run",
                query=UserQuery(text="test"),
                corpus_package=DataCorpusPackage(),
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
