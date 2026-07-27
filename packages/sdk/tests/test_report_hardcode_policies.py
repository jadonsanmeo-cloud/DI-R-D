import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report import (
    ChartAgent,
    DataScienceProcessor,
    LocalePolicy,
    PlanAgent,
    ReportAgent,
    ReportAssetPolicy,
    ReportEngine,
    ReportFormatRegistry,
    ReportRenderer,
    RouterAgent,
    TemplateAgent,
    TemplatePool,
)
from data_intelligence_sdk.runtime.sandbox import SandboxEnvironment


class ReportHardcodePolicyTests(unittest.TestCase):
    def test_explicit_document_scope_creates_goal_evidence_retrieval(self):
        plan = PlanAgent(None)._fallback_plan(
            ExecutionSpec(
                intent="report",
                objective="Analyze the selected contract",
                constraints={
                    "selected_data_context": {
                        "selected_documents": ["contract-42"],
                    }
                },
            ),
            DataCorpusPackage(),
            None,
            [],
        )

        step = next(
            item for item in plan["steps"] if item["step_id"] == "retrieve-contract-42"
        )
        self.assertTrue(step["required"])
        self.assertEqual(
            step["operation"]["kind"],
            "source.document.retrieve",
        )
        self.assertIn("goal_evidence", step["outputs"][0]["semantic_roles"])

    def test_explicit_vector_scope_creates_goal_evidence_retrieval(self):
        plan = PlanAgent(None)._fallback_plan(
            ExecutionSpec(
                intent="report",
                objective="Analyze selected knowledge",
                constraints={
                    "selected_data_context": {
                        "selected_vector_collections": ["knowledge"],
                    }
                },
            ),
            DataCorpusPackage(
                schemas={"vector_collections": {"knowledge": {"columns": ["text"]}}}
            ),
            None,
            [],
        )

        step = next(
            item for item in plan["steps"] if item["step_id"] == "retrieve-knowledge"
        )
        self.assertTrue(step["required"])
        self.assertEqual(step["operation"]["kind"], "source.vector.retrieve")
        self.assertIn("goal_evidence", step["outputs"][0]["semantic_roles"])

    def test_router_uses_capability_id_when_tool_name_changes(self):
        route = RouterAgent(None).run(
            {
                "step_id": "read-book",
                "operation": {"kind": "read_spreadsheet"},
            },
            [
                {
                    "tool_name": "workbook_reader_v2",
                    "capability_names": ["source.spreadsheet.materialize"],
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            ["book.xlsx"],
        )

        self.assertEqual(route["tool_name"], "workbook_reader_v2")
        self.assertEqual(
            route["capability_id"],
            "source.spreadsheet.materialize",
        )

    def test_csv_materialization_uses_registered_path_tool(self):
        route = RouterAgent(None).run(
            {
                "step_id": "materialize-csv",
                "operation": {"kind": "materialize_csv"},
            },
            [
                {
                    "tool_name": "scan_csv",
                    "capability_names": ["source.csv.scan"],
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            ["data.csv"],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["tool_name"], "scan_csv")
        self.assertEqual(route["arguments"], {"path": "data.csv"})

    def test_csv_materialization_rejects_text_converter_for_file_path(self):
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "csv_to_json",
                "arguments": {"csv_text": "data.csv"},
            },
            {
                "step_id": "materialize-csv",
                "operation": {"kind": "materialize_csv"},
            },
            [
                {
                    "tool_name": "csv_to_json",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"csv_text": {"type": "string"}},
                    },
                }
            ],
            ["data.csv"],
        )

        self.assertEqual(route["route"], "generate_tool")
        self.assertIsNone(route["tool_name"])
        self.assertEqual(route["arguments"], {})

    def test_router_rejects_source_path_bound_to_content_parameter(self):
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "csv_to_json",
                "arguments": {"csv_text": "data.csv"},
            },
            {
                "step_id": "assemble-report",
                "operation": {"kind": "assemble_report_payload"},
            },
            [
                {
                    "tool_name": "csv_to_json",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"csv_text": {"type": "string"}},
                    },
                }
            ],
            ["data.csv"],
        )

        self.assertEqual(route["route"], "generate_tool")
        self.assertIsNone(route["tool_name"])
        self.assertEqual(route["arguments"], {})

    def test_document_materialization_does_not_guess_unregistered_method(self):
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "list_datasets",
                "arguments": {},
            },
            {
                "step_id": "materialize-document",
                "operation": {"kind": "materialize_document"},
            },
            [
                {
                    "tool_name": "list_datasets",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {},
                    },
                }
            ],
            ["report.html"],
        )

        self.assertEqual(route["route"], "generate_tool")
        self.assertIsNone(route["tool_name"])

    def test_router_rejects_method_arguments_with_wrong_schema_type(self):
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "get_dataset_metadata",
                "arguments": {"dataset_id": []},
            },
            {
                "step_id": "extract-metadata",
                "operation": {"kind": "extract_metadata"},
            },
            [
                {
                    "tool_name": "get_dataset_metadata",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"dataset_id": {"type": "string"}},
                        "required": ["dataset_id"],
                    },
                }
            ],
            ["report.html"],
        )

        self.assertEqual(route["route"], "generate_tool")
        self.assertIsNone(route["tool_name"])

    def test_router_allows_actual_content_for_content_parameter(self):
        route = RouterAgent(None)._normalize_route(
            {
                "route": "existing_tool",
                "tool_name": "csv_to_json",
                "arguments": {"csv_text": "Year,Annual\n2025,42"},
            },
            {
                "step_id": "convert-inline-csv",
                "operation": {"kind": "convert"},
            },
            [
                {
                    "tool_name": "csv_to_json",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"csv_text": {"type": "string"}},
                    },
                }
            ],
            ["data.csv"],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["arguments"]["csv_text"], "Year,Annual\n2025,42")

    def test_pdf_extractor_does_not_handle_aggregate_operation(self):
        route = RouterAgent(None).run(
            {
                "step_id": "aggregate",
                "description": "Aggregate monthly values",
                "operation": {"kind": "aggregate"},
                "required_data": {"tables": []},
            },
            [
                {
                    "tool_name": "extract_pdf_text",
                    "capability_names": ["source.pdf.extract_text"],
                }
            ],
            ["report.pdf"],
        )

        self.assertEqual(route["route"], "generate_tool")

    def test_unsupported_report_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported report output format"):
            ReportFormatRegistry().resolve("pdf")

    def test_template_pruning_keeps_required_and_goal_evidence_steps(self):
        plan = {
            "steps": [
                {
                    "step_id": "bound",
                    "depends_on": [],
                    "outputs": [{"name": "value", "semantic_roles": []}],
                },
                {
                    "step_id": "required",
                    "required": True,
                    "depends_on": [],
                    "outputs": [],
                },
                {
                    "step_id": "evidence",
                    "depends_on": [],
                    "outputs": [
                        {
                            "name": "content",
                            "semantic_roles": ["goal_evidence"],
                        }
                    ],
                },
                {
                    "step_id": "unused",
                    "depends_on": [],
                    "outputs": [],
                },
            ]
        }
        template = {
            "bindings": [
                {
                    "status": "resolved",
                    "plan_output_refs": ["step-output://bound/value"],
                }
            ]
        }

        execution_plan = ReportEngine._plan_for_template(plan, template)

        self.assertEqual(
            [step["step_id"] for step in execution_plan["steps"]],
            ["bound", "required", "evidence"],
        )

    def test_chart_policy_reports_truncation_and_empty_types_fallback(self):
        chart = DataScienceProcessor._chart_dataset(
            {
                "chart_data": {
                    "rows": [
                        {"category": f"item-{index}", "value": index}
                        for index in range(500)
                    ]
                }
            },
            [],
        )
        self.assertEqual(len(chart["rows"]), 40)
        self.assertTrue(chart["truncated"])
        fallback = ChartAgent(None)._fallback_chart(
            {
                "chart_id": "empty-types",
                "allowed_types": [],
                "dataset": {
                    "data": [{"category": "A", "value": 1}],
                },
            }
        )
        self.assertEqual(fallback["status"], "fallback")
        self.assertIsNone(fallback["selected_type"])

    def test_renderer_uses_locale_and_explicit_offline_asset_policy(self):
        html = next(
            item["content"]
            for item in ReportRenderer(
                asset_policy=ReportAssetPolicy(echarts_script_url=None)
            ).render(
                {
                    "title": "Báo cáo",
                    "summary": "",
                    "sections": [],
                    "sources": ["a.csv", "b.csv"],
                },
                locale_policy=LocalePolicy.for_locale("vi-VN"),
            )
            if item["format"] == "html"
        )
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn('lang="vi"', html)
        self.assertIn("Báo cáo Data Intelligence", html)

    def test_sandbox_capability_payload_is_versioned_and_service_driven(self):
        environment = SandboxEnvironment.from_payload(
            {
                "contract_version": "2.1",
                "runtime": "python",
                "runtime_version": "3.13",
                "available_packages": ["polars"],
                "network_access": True,
            }
        )

        payload = environment.to_prompt_payload()
        self.assertEqual(payload["contract_version"], "2.1")
        self.assertEqual(payload["runtime_version"], "3.13")
        self.assertEqual(payload["available_packages"], ["polars"])
        self.assertTrue(payload["network_access"])
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "analyze",
                "source_code": (
                    "import pandas\n"
                    "def analyze() -> list[dict]:\n"
                    "    return []\n"
                ),
                "parameters_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {},
            sandbox_environment=payload,
        )
        self.assertIn("pandas", errors[0])

    def test_missing_llm_selection_uses_configured_raw_template(self):
        result = TemplateAgent(None, TemplatePool()).run(
            ExecutionSpec(
                intent="report",
                objective="Analyze the monthly trend and growth",
            ),
            {"steps": []},
            DataCorpusPackage(sources=["sales.csv"]),
        )

        self.assertEqual(
            result["selection"]["template_id"],
            "adaptive-raw-report",
        )

    def test_chart_agent_omits_request_without_evidence_claim(self):
        result = ChartAgent(None).run(
            {
                "chart_id": "decorative",
                "analytical_purpose": "",
                "evidence_claim": "",
                "suggested_type": "bar",
                "allowed_types": ["bar"],
                "fallback": {"action": "omit"},
                "datasets": [
                    {
                        "schema": {
                            "fields": [
                                {"name": "category"},
                                {"name": "value"},
                            ]
                        },
                        "data": [
                            {"category": "A", "value": 1},
                            {"category": "B", "value": 2},
                        ],
                    }
                ],
            }
        )

        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["fallback"]["action"], "omit")
        self.assertFalse(result["option"])

    def test_content_role_supports_localized_block_names(self):
        text = ReportAgent._report_text_for_block(
            {
                "block_id": "gioi-han",
                "title": "Giới hạn",
                "content_role": "limitation",
            },
            [
                {
                    "report_content": {
                        "limitations": ["Dữ liệu chưa bao gồm quý IV."]
                    }
                }
            ],
            [],
        )

        self.assertIn("Dữ liệu chưa bao gồm quý IV.", text)


if __name__ == "__main__":
    unittest.main()
