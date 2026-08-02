import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report import (
    ChartAgent,
    CodeAgent,
    DataScienceProcessor,
    LocalePolicy,
    PlanAgent,
    ReportAgent,
    ReportAssetPolicy,
    ReportEngine,
    ReportFormatRegistry,
    ReportPresentationPolicy,
    ReportRenderer,
    RouterAgent,
    SemanticAnalysisAgent,
    TemplateAgent,
    TemplatePool,
)
from data_intelligence_sdk.engines.reporting.contracts import (
    ReportContractValidator,
    ToolArgumentBinder,
)
from data_intelligence_sdk.engines.reporting.execution import DataScienceAgent
from data_intelligence_sdk.engines.reporting.utils import (
    _bind_dependency_inputs,
    _normalize_plan_inputs,
    _normalize_plan_outputs,
)
from data_intelligence_sdk.runtime.sandbox import SandboxEnvironment


class ReportHardcodePolicyTests(unittest.TestCase):
    def test_multiline_metric_transcript_is_not_accepted_as_analysis(self):
        self.assertTrue(
            ReportAgent._looks_like_metric_transcript(
                "Metric: 10 during period one\n"
                "Metric: 14 during period two\n"
                "Metric: 18 during period three"
            )
        )

    def test_chart_measure_excludes_retrieval_metadata(self):
        dataset = DataScienceProcessor._chart_dataset(
            {
                "chart_data": {
                    "render": True,
                    "rows": [
                        {"period": "one", "amount": 10, "relevance_score": 0.9},
                        {"period": "two", "amount": 14, "relevance_score": 0.8},
                    ],
                    "encoding": {
                        "dimension": "period",
                        "measure": "relevance_score",
                    },
                }
            },
            [],
        )

        self.assertTrue(dataset["render"])
        self.assertEqual(dataset["encoding"]["measures"], ["amount"])

    def test_semantic_capability_contract_overrides_generated_code_label(self):
        operation = PlanAgent._normalize_operation_contract(
            {
                "kind": "extract_evidence",
                "capability": "semantic_extraction",
                "execution_class": "deterministic_transform",
                "execution_mode": "generated_code",
                "deterministic_spec": {"procedure": "interpret the document"},
            }
        )
        step = {"operation": operation}
        PlanAgent._align_execution_modes_with_lineage([step])

        self.assertEqual(operation["execution_class"], "semantic_inference")
        self.assertEqual(operation["execution_mode"], "semantic_analysis")

    def test_auto_request_over_ingested_document_requires_semantic_inference(self):
        steps = [
            {
                "step_id": "materialize",
                "inputs": [],
                "operation": {
                    "kind": "corpus_ingested_document_materialize",
                    "execution_class": "source_operation",
                    "execution_mode": "method_hub",
                },
            },
            {
                "step_id": "analyze",
                "inputs": [
                    {
                        "name": "source",
                        "ref": "step-output://materialize/source_content",
                    }
                ],
                "operation": {
                    "kind": "template_data_request",
                    "execution_class": "auto",
                    "execution_mode": "auto",
                },
            },
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        operation = steps[1]["operation"]
        self.assertEqual(operation["execution_class"], "semantic_inference")
        self.assertEqual(operation["execution_mode"], "semantic_analysis")

    def test_report_metric_normalization_uses_structural_aliases(self):
        metrics = ReportAgent._normalize_block_metrics(
            [
                {"label": "Queue depth", "value": 12},
                {"metric_name": "Coverage", "metric_value": "84%"},
                {"value": 99},
            ]
        )

        self.assertEqual(
            [(item["name"], item["value"]) for item in metrics],
            [("Queue depth", 12), ("Coverage", "84%")],
        )

    def test_report_normalizes_serialized_evidence_without_domain_rules(self):
        text = ReportAgent._normalize_generated_text(
            "{'statement': 'Coverage is incomplete', "
            "'source_location': 'page 2'}"
        )

        self.assertEqual(text, "Coverage is incomplete Source: page 2.")

    def test_report_accepts_structured_recommendation_items(self):
        fallback = {
            "title": "Evidence Review",
            "sections": [
                {
                    "section_id": "actions",
                    "blocks": [
                        {
                            "block_id": "decision-actions",
                            "type": "recommendations",
                            "status": "no_data",
                            "content": {"text": ""},
                        }
                    ],
                }
            ],
        }
        payload = {
            "sections": [
                {
                    "section_id": "actions",
                    "blocks": [
                        {
                            "block_id": "decision-actions",
                            "type": "recommendations",
                            "content": {
                                "items": [
                                    {
                                        "title": "Validate first",
                                        "text": "Run a bounded evidence check.",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ]
        }

        aligned = ReportAgent._align_structured_payload(payload, fallback)

        block = aligned["sections"][0]["blocks"][0]
        self.assertEqual(
            block["content"]["text"],
            "Validate first: Run a bounded evidence check.",
        )
        self.assertEqual(block["status"], "completed")

    def test_structured_actions_keep_decision_rationale_and_validation(self):
        text = ReportAgent._format_report_items(
            [
                {
                    "title": "Validate capacity",
                    "action": "Run a bounded stress test before changing limits.",
                    "rationale": "The observation window is short.",
                    "risk": "The test may not cover seasonal demand.",
                    "validation_signal": "No saturation at the agreed peak load.",
                }
            ]
        )

        self.assertIn("Run a bounded stress test", text)
        self.assertIn("Rationale: The observation window is short.", text)
        self.assertIn("Risk: The test may not cover seasonal demand.", text)
        self.assertIn("Validation signal: No saturation", text)

    def test_code_agent_accepts_nested_structural_code_alias(self):
        class AliasCodeAgent(CodeAgent):
            def _invoke_text(self, **inputs):
                del inputs
                return (
                    '{"tool_name":"extract-values",'
                    '"implementation":{"python_code":'
                    '"def extract_values(rows):\\n    return rows"}}'
                )

        result = AliasCodeAgent(None).run(
            {"step_id": "extract-values"},
            {"sources": []},
        )

        self.assertEqual(result["tool_name"], "extract_values")
        self.assertIn("def extract_values", result["source_code"])
        self.assertNotIn("generation_error", result)

    def test_code_agent_accepts_raw_fenced_python_response(self):
        class FencedCodeAgent(CodeAgent):
            def _invoke_text(self, **inputs):
                del inputs
                return "```python\ndef transform(records):\n    return records\n```"

        result = FencedCodeAgent(None).run(
            {"step_id": "transform-records"},
            {"sources": []},
        )

        self.assertEqual(result["tool_name"], "transform")
        self.assertIn("def transform", result["source_code"])
        self.assertNotIn("generation_error", result)

    def test_code_agent_detects_python_in_unknown_structural_field(self):
        class UnknownFieldCodeAgent(CodeAgent):
            def _invoke_text(self, **inputs):
                del inputs
                return (
                    '{"result":{"generated_artifact":'
                    '"def compute(items):\\n    return list(items)"}}'
                )

        result = UnknownFieldCodeAgent(None).run(
            {"step_id": "compute"},
            {"sources": []},
        )

        self.assertEqual(result["tool_name"], "compute")
        self.assertIn("def compute", result["source_code"])
        self.assertNotIn("generation_error", result)

    def test_code_agent_reports_structured_response_without_source(self):
        class EmptyCodeAgent(CodeAgent):
            def _invoke_text(self, **inputs):
                del inputs
                return '{"tool_name":"empty"}'

        result = EmptyCodeAgent(None).run(
            {"step_id": "empty-step"},
            {"sources": []},
        )

        self.assertEqual(result["source_code"], "")
        self.assertIn("without Python source", result["generation_error"])

    def test_code_agent_uses_declared_operation_entrypoint_with_helpers(self):
        class HelperCodeAgent(CodeAgent):
            def _invoke_text(self, **inputs):
                del inputs
                return (
                    '{"tool_name":"generated-step",'
                    '"source_code":"def transform(rows):\\n    return rows\\n\\n'
                    'def _helper(value):\\n    return value"}'
                )

        result = HelperCodeAgent(None).run(
            {
                "step_id": "generated-step",
                "operation": {"kind": "transform"},
            },
            {"sources": []},
        )

        self.assertEqual(result["tool_name"], "transform")

    def test_legacy_step_uri_is_canonicalized_without_duplicate_prefix(self):
        steps = [
            {
                "step_id": "source",
                "required": True,
                "depends_on": [],
                "outputs": [{"name": "content", "type": "array"}],
            },
            {
                "step_id": "analyze",
                "depends_on": ["source"],
                "inputs": [
                    {
                        "name": "source_data",
                        "ref": "step://source/content",
                    }
                ],
                "outputs": [{"name": "analysis", "type": "object"}],
            },
        ]

        _bind_dependency_inputs(steps)

        self.assertEqual(
            steps[1]["inputs"][0]["ref"],
            "step-output://source/content",
        )
        self.assertEqual(
            ReportContractValidator().validate_plan({"steps": steps}),
            [],
        )

    def test_dependency_relative_output_ref_is_canonicalized(self):
        steps = [
            {
                "step_id": "source",
                "required": True,
                "depends_on": [],
                "outputs": [{"name": "content", "type": "array"}],
            },
            {
                "step_id": "analyze",
                "depends_on": ["source"],
                "inputs": [{"name": "records", "ref": "source/content"}],
                "outputs": [{"name": "analysis", "type": "object"}],
            },
        ]

        _bind_dependency_inputs(steps)

        self.assertEqual(
            steps[1]["inputs"][0]["ref"],
            "step-output://source/content",
        )

    def test_plan_input_alias_is_canonicalized_as_reference(self):
        inputs = _normalize_plan_inputs(
            [
                {
                    "name": "evidence",
                    "input": "step-output://extract/evidence",
                    "required": True,
                }
            ]
        )

        self.assertEqual(inputs[0]["ref"], "step-output://extract/evidence")

    def test_dependency_binding_deduplicates_same_name_and_reference(self):
        steps = [
            {
                "step_id": "extract",
                "required": True,
                "depends_on": [],
                "inputs": [],
                "outputs": [{"name": "evidence", "type": "array"}],
            },
            {
                "step_id": "combine",
                "required": True,
                "depends_on": ["extract"],
                "inputs": [
                    {
                        "name": "evidence",
                        "input": "step-output://extract/evidence",
                    },
                    {
                        "name": "evidence",
                        "ref": "step-output://extract/evidence",
                    },
                ],
                "outputs": [{"name": "combined", "type": "array"}],
            },
        ]

        _bind_dependency_inputs(steps)

        self.assertEqual(len(steps[1]["inputs"]), 1)
        self.assertEqual(
            steps[1]["inputs"][0]["ref"],
            "step-output://extract/evidence",
        )

    def test_plan_agent_receives_validation_feedback_for_repair(self):
        class FeedbackPlanAgent(PlanAgent):
            def _invoke_json(self, **inputs):
                self.invocation_inputs = inputs
                return {"steps": []}

        agent = FeedbackPlanAgent(None)
        agent.run(
            ExecutionSpec(intent="report", objective="Analyze evidence"),
            DataCorpusPackage(),
            validation_feedback=["duplicate input names"],
        )

        self.assertEqual(
            agent.invocation_inputs["validation_feedback"],
            ["duplicate input names"],
        )

    def test_router_can_correct_deterministic_label_to_semantic_route(self):
        route = RouterAgent._enforce_execution_class(
            {
                "route": "semantic_analysis",
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": "The resolved input requires language understanding.",
            },
            "deterministic_transform",
        )

        self.assertEqual(route["route"], "semantic_analysis")

    def test_semantic_agent_accepts_direct_array_output(self):
        normalized = SemanticAnalysisAgent._normalize_payload(
            [{"claim": "Supported finding"}],
            {
                "outputs": [
                    {"name": "findings", "type": "array", "shape": "table"}
                ]
            },
        )

        self.assertEqual(normalized["status"], "completed")
        self.assertEqual(normalized["output"], [{"claim": "Supported finding"}])

    def test_semantic_agent_accepts_direct_named_outputs(self):
        normalized = SemanticAnalysisAgent._normalize_payload(
            {"findings": [{"claim": "Supported finding"}], "risks": []},
            {
                "outputs": [
                    {"name": "findings", "type": "array", "shape": "table"},
                    {"name": "risks", "type": "array", "shape": "table"},
                ]
            },
        )

        self.assertEqual(
            normalized["output"],
            {"findings": [{"claim": "Supported finding"}], "risks": []},
        )

    def test_multi_output_mapping_expands_into_atomic_contracts(self):
        outputs = _normalize_plan_outputs(
            [
                {
                    "incident_records": {
                        "type": "table",
                        "shape": {
                            "columns": ["period", "incident_count"],
                            "max_rows": 30,
                        },
                        "semantic_roles": ["goal_evidence"],
                    },
                    "risk_factors": {
                        "type": "table",
                        "shape": {
                            "columns": ["risk", "evidence"],
                            "max_rows": 20,
                        },
                        "semantic_roles": ["goal_evidence"],
                    },
                }
            ],
            "extract",
        )

        self.assertEqual(
            [item["name"] for item in outputs],
            ["incident_records", "risk_factors"],
        )
        self.assertTrue(all(item["type"] == "array" for item in outputs))
        self.assertTrue(all(item["shape"] == "table" for item in outputs))

        schema = ReportEngine._step_output_schema(
            {"step_id": "extract", "required": True, "outputs": outputs},
            {},
        )
        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            schema["required"],
            ["incident_records", "risk_factors"],
        )
        self.assertEqual(
            ReportEngine._output_contract_errors(
                {"incident_records": [], "risk_factors": []},
                schema,
            ),
            [],
        )
        self.assertIn(
            "must have JSON type array",
            "; ".join(
                ReportEngine._output_contract_errors(
                    {"incident_records": {}, "risk_factors": []},
                    schema,
                )
            ),
        )

    def test_noncanonical_shape_is_derived_from_json_type(self):
        outputs = _normalize_plan_outputs(
            [
                {
                    "name": "evidence",
                    "type": "array",
                    "shape": "model_specific_capacity_hint",
                    "semantic_roles": ["goal_evidence"],
                }
            ],
            "extract",
        )

        self.assertEqual(outputs[0]["type"], "array")
        self.assertEqual(outputs[0]["shape"], "table")

    def test_multi_input_mapping_preserves_exact_lineage_and_optionality(self):
        steps = [
            {
                "step_id": "extract",
                "required": False,
                "depends_on": [],
                "inputs": [],
                "outputs": _normalize_plan_outputs(
                    [
                        {"name": "metrics", "type": "table"},
                        {"name": "risks", "type": "table"},
                    ],
                    "extract",
                ),
                "operation": {
                    "execution_mode": "semantic_analysis",
                    "execution_class": "semantic_inference",
                },
            },
            {
                "step_id": "combine",
                "required": True,
                "depends_on": ["extract"],
                "inputs": [
                    {
                        "metric_input": "extract.metrics",
                        "risk_input": "extract.risks",
                    }
                ],
                "outputs": _normalize_plan_outputs(
                    [{"name": "combined", "type": "table"}],
                    "combine",
                ),
                "operation": {
                    "execution_mode": "generated_code",
                    "execution_class": "deterministic_transform",
                },
            },
        ]

        _bind_dependency_inputs(steps)

        inputs = _normalize_plan_inputs(steps[1]["inputs"])
        self.assertEqual(
            [item["ref"] for item in inputs],
            [
                "step-output://extract/metrics",
                "step-output://extract/risks",
            ],
        )
        self.assertTrue(all(item["required"] is False for item in inputs))
        self.assertEqual(ReportContractValidator().validate_plan({"steps": steps}), [])

    def test_plan_validator_rejects_unknown_exact_output_reference(self):
        plan = {
            "steps": [
                {
                    "step_id": "source",
                    "depends_on": [],
                    "inputs": [],
                    "outputs": [
                        {
                            "name": "records",
                            "type": "array",
                            "shape": "table",
                        }
                    ],
                    "operation": {
                        "execution_mode": "method_hub",
                        "execution_class": "source_operation",
                    },
                },
                {
                    "step_id": "analyze",
                    "depends_on": ["source"],
                    "inputs": [
                        {
                            "name": "records",
                            "ref": "step-output://source/not-declared",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "name": "evidence",
                            "type": "array",
                            "shape": "table",
                        }
                    ],
                    "operation": {
                        "execution_mode": "semantic_analysis",
                        "execution_class": "semantic_inference",
                    },
                },
            ]
        }

        errors = ReportContractValidator().validate_plan(plan)

        self.assertIn(
            "references unavailable output",
            "; ".join(errors),
        )

    def test_failed_execution_bypasses_datascience_analysis(self):
        self.assertEqual(
            ReportEngine._execution_analysis_choice(
                {"execution_result": {"status": "failed"}}
            ),
            "failed",
        )
        self.assertEqual(
            ReportEngine._execution_analysis_choice(
                {"execution_result": {"status": "completed"}}
            ),
            "analyze",
        )

    def test_operation_kind_may_declare_the_execution_class_contract(self):
        operation = PlanAgent._normalize_operation_contract(
            {"kind": "semantic_inference"}
        )

        self.assertEqual(operation["execution_class"], "semantic_inference")

    def test_report_content_normalization_preserves_recommendations(self):
        normalized = DataScienceProcessor._normalize_report_content(
            {
                "analysis_summary": "Summary",
                "report_content": {
                    "recommendations": [
                        {
                            "title": "Validate capacity",
                            "statement": "Run a load test before the next peak.",
                        }
                    ]
                },
            }
        )

        self.assertEqual(
            normalized["recommendations"][0]["title"],
            "Validate capacity",
        )

    def test_document_evidence_inference_cannot_use_generated_regex_code(self):
        steps = [
            {
                "step_id": "materialize",
                "depends_on": [],
                "required_data": {"documents": ["document-1"]},
                "operation": {
                    "kind": "corpus_ingested_document_materialize",
                    "execution_mode": "method_hub",
                    "execution_class": "source_operation",
                },
                "outputs": [
                    {
                        "name": "content",
                        "semantic_roles": ["source_content"],
                    }
                ],
            },
            {
                "step_id": "extract",
                "description": "Extract objective-relevant evidence from the text.",
                "depends_on": ["materialize"],
                "operation": {
                    "kind": "extract_goal_evidence",
                    "capability": "evidence_extraction",
                    "execution_mode": "generated_code",
                    "execution_class": "semantic_inference",
                },
                "outputs": [
                    {
                        "name": "evidence",
                        "semantic_roles": ["goal_evidence"],
                    }
                ],
            },
            {
                "step_id": "reshape",
                "description": "Reshape extracted evidence into rows.",
                "depends_on": ["extract"],
                "operation": {
                    "kind": "table_construct",
                    "execution_mode": "generated_code",
                    "execution_class": "deterministic_transform",
                    "deterministic_spec": {
                        "procedure": (
                            "Copy each extracted evidence record into one output "
                            "row without changing its values."
                        )
                    },
                },
                "outputs": [
                    {
                        "name": "rows",
                        "semantic_roles": ["analysis_data"],
                    }
                ],
            },
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[1]["operation"]["execution_mode"],
            "semantic_analysis",
        )
        self.assertEqual(
            steps[2]["operation"]["execution_mode"],
            "generated_code",
        )

    def test_auto_semantic_contract_bypasses_method_and_code_routing(self):
        route = RouterAgent(None).run(
            {
                "step_id": "identify-risks",
                "description": "Identify evidence-backed operational risks.",
                "operation": {
                    "kind": "identify_risks",
                    "execution_mode": "auto",
                    "execution_class": "semantic_inference",
                },
                "outputs": [
                    {
                        "name": "risks",
                        "semantic_roles": ["goal_evidence", "risk"],
                    }
                ],
            },
            [
                {
                    "tool_name": "numeric_transform",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {"values": {"type": "array"}},
                    },
                }
            ],
            [],
        )

        self.assertEqual(route["route"], "semantic_analysis")

    def test_router_does_not_infer_semantics_from_task_keywords(self):
        route = RouterAgent(None).run(
            {
                "step_id": "free-form-step",
                "description": "Summarize and interpret the material.",
                "operation": {
                    "kind": "custom_operation",
                    "execution_mode": "auto",
                    "execution_class": "auto",
                },
                "outputs": [
                    {
                        "name": "summary",
                        "semantic_roles": ["summary"],
                    }
                ],
            },
            [],
            [],
        )

        self.assertEqual(route["route"], "unsupported")

    def test_execution_class_overrides_a_conflicting_generated_mode(self):
        route = RouterAgent(None).run(
            {
                "step_id": "interpret",
                "operation": {
                    "kind": "custom_interpretation",
                    "execution_mode": "generated_code",
                    "execution_class": "semantic_inference",
                },
                "outputs": [{"name": "evidence"}],
            },
            [],
            [],
        )

        self.assertEqual(route["route"], "semantic_analysis")

    def test_deterministic_table_construct_with_parameters_remains_generated_code(self):
        steps = [
            {
                "step_id": "construct",
                "description": "Construct rows from already extracted fields.",
                "depends_on": [],
                "operation": {
                    "kind": "table_construct",
                    "execution_mode": "generated_code",
                    "execution_class": "deterministic_transform",
                    "parameters": {"columns": ["name", "value"]},
                },
                "outputs": [
                    {
                        "name": "evidence",
                        "semantic_roles": ["goal_evidence"],
                    }
                ],
            }
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[0]["operation"]["execution_mode"],
            "generated_code",
        )

    def test_run_local_procedure_name_can_route_to_generated_code(self):
        steps = [
            {
                "step_id": "derive",
                "depends_on": [],
                "operation": {
                    "execution_class": "deterministic_transform",
                    "execution_mode": "generated_code",
                    "deterministic_spec": {
                        "procedure": "calculate_operational_metrics"
                    },
                },
                "outputs": [{"name": "metrics"}],
            }
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[0]["operation"]["execution_class"],
            "deterministic_transform",
        )
        self.assertEqual(
            steps[0]["operation"]["execution_mode"],
            "generated_code",
        )

    def test_run_local_literal_expression_can_route_to_generated_code(self):
        steps = [
            {
                "step_id": "derive",
                "depends_on": [],
                "operation": {
                    "execution_class": "deterministic_transform",
                    "execution_mode": "generated_code",
                    "deterministic_spec": {
                        "procedure": "extract_metrics(document)",
                        "expression": (
                            'return {"metrics": [{"name": "Revenue", '
                            '"value": 1480000}]}'
                        ),
                    },
                },
                "outputs": [{"name": "metrics"}],
            }
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[0]["operation"]["execution_class"],
            "deterministic_transform",
        )

    def test_unstructured_transform_without_procedure_becomes_semantic(self):
        steps = [
            {
                "step_id": "materialize",
                "depends_on": [],
                "operation": {
                    "execution_class": "source_operation",
                    "execution_mode": "method_hub",
                },
                "outputs": [
                    {
                        "name": "content",
                        "semantic_roles": ["source_content"],
                    }
                ],
            },
            {
                "step_id": "derive",
                "depends_on": ["materialize"],
                "operation": {
                    "execution_class": "deterministic_transform",
                    "execution_mode": "generated_code",
                },
                "outputs": [{"name": "evidence"}],
            },
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[1]["operation"]["execution_class"],
            "semantic_inference",
        )
        self.assertEqual(
            steps[1]["operation"]["execution_mode"],
            "semantic_analysis",
        )

    def test_unstructured_transform_with_procedure_remains_deterministic(self):
        steps = [
            {
                "step_id": "materialize",
                "depends_on": [],
                "operation": {
                    "execution_class": "source_operation",
                    "execution_mode": "method_hub",
                },
                "outputs": [
                    {
                        "name": "content",
                        "semantic_roles": ["source_content"],
                    }
                ],
            },
            {
                "step_id": "derive",
                "depends_on": ["materialize"],
                "operation": {
                    "execution_class": "deterministic_transform",
                    "execution_mode": "generated_code",
                    "deterministic_spec": {
                        "procedure": "Count characters in each supplied text value."
                    },
                },
                "outputs": [{"name": "counts"}],
            },
        ]

        PlanAgent._align_execution_modes_with_lineage(steps)

        self.assertEqual(
            steps[1]["operation"]["execution_class"],
            "deterministic_transform",
        )
        self.assertEqual(
            steps[1]["operation"]["execution_mode"],
            "generated_code",
        )

    def test_explicit_document_scope_creates_source_materialization(self):
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
        self.assertEqual(step["outputs"][0]["semantic_roles"], ["source_content"])
        self.assertEqual(step["outputs"][0]["type"], "array")
        self.assertEqual(ReportContractValidator().validate_plan(plan), [])

    def test_explicit_vector_scope_creates_source_materialization(self):
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
        self.assertEqual(step["outputs"][0]["semantic_roles"], ["source_content"])

    def test_request_resolution_rejects_incompatible_explicit_source_ref(self):
        feedback = [
            {
                "request_id": "provide-evidence",
                "requirement_ref": "goal-evidence",
                "expected_output": {"shape": "table"},
                "semantic_roles": {"measures": ["goal_evidence"]},
            }
        ]
        steps = [
            {
                "step_id": "source",
                "outputs": [
                    {
                        "name": "content",
                        "shape": "table",
                        "semantic_roles": ["source_content"],
                    }
                ],
            },
            {
                "step_id": "analyze",
                "outputs": [
                    {
                        "name": "evidence",
                        "shape": "table",
                        "semantic_roles": ["goal_evidence"],
                    }
                ],
            },
        ]

        resolutions = PlanAgent._normalize_request_resolutions(
            {
                "request_id": "provide-evidence",
                "output_refs": ["step-output://source/content"],
            },
            feedback,
            steps,
        )

        self.assertEqual(
            resolutions[0]["output_refs"],
            ["step-output://analyze/evidence"],
        )

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

    def test_tool_binder_rejects_method_arguments_with_wrong_schema_type(self):
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

        binding = ToolArgumentBinder().bind(
            route,
            {
                "type": "object",
                "properties": {"dataset_id": {"type": "string"}},
                "required": ["dataset_id"],
            },
            [],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertIn(
            "does not satisfy the Method Hub schema",
            "; ".join(binding.errors),
        )

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
                "operation": {
                    "kind": "aggregate",
                    "execution_class": "deterministic_transform",
                },
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


    def test_report_content_preserves_run_local_block_assignments(self):
        normalized = DataScienceProcessor._normalize_report_content(
            {
                "analysis_summary": "General fallback.",
                "report_content": {
                    "block_content": {
                        "objective-specific-analysis": {
                            "text": "This block answers its own analytical question.",
                            "items": [
                                {
                                    "title": "Observed pattern",
                                    "statement": "The evidence is specific to this block.",
                                }
                            ],
                        }
                    }
                },
            }
        )

        self.assertEqual(
            normalized["block_content"]["objective-specific-analysis"]["text"],
            "This block answers its own analytical question.",
        )
        result = {"report_content": normalized}
        self.assertEqual(
            ReportAgent._report_text_for_block(
                {
                    "block_id": "objective-specific-analysis",
                    "content_role": "narrative",
                },
                [result],
                [],
            ),
            "This block answers its own analytical question.",
        )

    def test_chart_dataset_accepts_declared_arbitrary_fields_and_measures(self):
        dataset = DataScienceProcessor._chart_dataset(
            {
                "chart_data": {
                    "render": True,
                    "title": "Service outcomes by interval",
                    "rows": [
                        {"interval": "A", "throughput": 18, "failures": 2},
                        {"interval": "B", "throughput": 24, "failures": 1},
                    ],
                    "encoding": {
                        "dimension": "interval",
                        "measures": ["throughput", "failures"],
                    },
                    "measures": [
                        {"field": "throughput", "label": "Completed work"},
                        {"field": "failures", "label": "Failed work"},
                    ],
                }
            },
            [],
        )

        self.assertTrue(dataset["render"])
        self.assertEqual(dataset["encoding"]["dimension"], "interval")
        self.assertEqual(
            dataset["encoding"]["measures"],
            ["throughput", "failures"],
        )
        self.assertEqual(
            dataset["rows"][0],
            {"interval": "A", "throughput": 18, "failures": 2},
        )

    def test_template_requirement_exposes_run_local_consumer_blocks(self):
        template_instance = {
            "bindings": [
                {
                    "status": "resolved",
                    "requirement_ref": "goal-evidence",
                    "plan_output_refs": ["step-output://analyze/evidence"],
                    "expected_output": {"shape": "table"},
                    "semantic_roles": {"measures": ["goal_evidence"]},
                }
            ],
            "sections": [
                {
                    "section_id": "analysis",
                    "title": "Run-local analysis",
                    "purpose": "Answer the selected analytical question.",
                    "blocks": [
                        {
                            "block_id": "selected-question",
                            "type": "narrative",
                            "content_role": "narrative",
                            "title": "Selected question",
                            "instructions": [
                                "Use only evidence relevant to this question."
                            ],
                            "required": True,
                            "data_requirement_refs": ["goal-evidence"],
                        }
                    ],
                }
            ],
        }

        requirements = ReportEngine.__new__(
            ReportEngine
        )._template_requirements_for_step(
            template_instance,
            {"step_id": "analyze"},
        )

        self.assertEqual(
            requirements[0]["consumer_blocks"][0]["block_id"],
            "selected-question",
        )
        self.assertEqual(
            requirements[0]["consumer_blocks"][0]["section_purpose"],
            "Answer the selected analytical question.",
        )

    def test_presentation_density_is_policy_driven(self):
        policy = ReportPresentationPolicy(max_kpi_items=2, max_table_rows=1)
        aggregated = DataScienceProcessor._overview_aggregated_data(
            {"first": 1, "second": 2, "third": 3},
            [],
            max_metrics=policy.max_kpi_items,
        )
        self.assertEqual(
            [name for name in aggregated if name != "source_context"],
            ["first", "second"],
        )

        html = next(
            item["content"]
            for item in ReportRenderer(presentation_policy=policy).render(
                {
                    "title": "Policy-driven report",
                    "summary": "Summary",
                    "sections": [
                        {
                            "section_id": "metrics",
                            "title": "Metrics",
                            "blocks": [
                                {
                                    "block_id": "metrics",
                                    "type": "kpi_group",
                                    "content": {
                                        "metrics": [
                                            {"name": "first", "value": 1},
                                            {"name": "second", "value": 2},
                                            {"name": "third", "value": 3},
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                    "warnings": [],
                }
            )
            if item["format"] == "html"
        )
        self.assertIn("First", html)
        self.assertIn("Second", html)
        self.assertNotIn("Third", html)

    def test_evidence_roles_bind_to_compatible_run_local_blocks(self):
        normalized = DataScienceProcessor._normalize_report_content(
            {
                "analysis_summary": "A grounded answer.",
                "report_content": {
                    "evidence_items": [
                        {
                            "title": "Observed change",
                            "statement": "The measured value increased.",
                            "content_roles": ["finding"],
                        }
                    ]
                },
            },
            [
                {
                    "consumer_blocks": [
                        {
                            "block_id": "run-local-findings",
                            "type": "insight_grid",
                            "content_role": "key_findings",
                        }
                    ]
                }
            ],
        )

        self.assertEqual(
            normalized["evidence_items"][0]["content_roles"],
            ["key_findings"],
        )
        self.assertEqual(
            normalized["block_content"]["run-local-findings"]["items"][0][
                "statement"
            ],
            "The measured value increased.",
        )

    def test_multi_output_fallback_preserves_evidence_and_builds_honest_chart(self):
        step = {
            "step_id": "analyze",
            "outputs": [
                {
                    "name": "findings_output",
                    "semantic_roles": ["headline_metric"],
                },
                {
                    "name": "trend_output",
                    "semantic_roles": ["analysis_data"],
                },
            ],
        }
        raw_data = {
            "findings_output": [
                {
                    "finding_category": "Observed pattern",
                    "finding_text": "The validated measure improved across the period.",
                    "document_location": "page 1",
                }
            ],
            "trend_output": [
                {"interval": "A", "measure_one": 10, "measure_two": 4},
                {"interval": "B", "measure_one": 15, "measure_two": 3},
                {"interval": "C", "measure_one": 20, "measure_two": 2},
            ],
        }

        result = DataScienceAgent(None)._fallback_analysis(
            step,
            {"profile": {"row_count": 4}, "artifact_ref": "artifact://result"},
            raw_data,
            [{"consumer_chart_ids": ["adaptive.chart"]}],
        )

        self.assertIn("validated measure improved", result["analysis_summary"])
        self.assertEqual(
            result["report_content"]["key_findings"][0]["title"],
            "Observed pattern",
        )
        self.assertTrue(result["chart_data"]["render"])
        self.assertEqual(result["chart_data"]["encoding"]["dimension"], "interval")
        self.assertIn("changed from 10 to 20", result["chart_data"]["evidence_claim"])

        analysis_rows = DataScienceProcessor._analysis_rows(step, raw_data)
        self.assertEqual(len(analysis_rows), 4)
        self.assertEqual(
            {row["output_name"] for row in analysis_rows},
            {"findings_output", "trend_output"},
        )

    def test_long_form_mixed_metrics_are_not_misrepresented_as_one_chart_series(self):
        chart_data = DataScienceAgent._fallback_chart_data(
            [
                (
                    "evidence",
                    "supporting_evidence",
                    {
                        "metric_name": "Metric A period 1",
                        "metric_value": 10,
                        "time_period": "Period 1",
                    },
                ),
                (
                    "evidence",
                    "supporting_evidence",
                    {
                        "metric_name": "Different metric period 2",
                        "metric_value": 20,
                        "time_period": "Period 2",
                    },
                ),
            ]
        )

        self.assertEqual(chart_data, {})

        item = DataScienceAgent._fallback_evidence_item(
            "evidence",
            "supporting_evidence",
            {
                "metric_name": "Validated total",
                "metric_value": 25,
                "time_period": "Covered period",
                "semantic_role": "goal_evidence",
            },
        )
        self.assertEqual(item["title"], "Validated total")
        self.assertEqual(item["statement"], "25 during Covered period")
        self.assertNotIn("Semantic role", item["statement"])

    def test_narrative_fallback_excludes_claims_already_used_in_summary(self):
        normalized = DataScienceProcessor._normalize_report_content(
            {
                "analysis_summary": "Finding A: Result A.",
                "report_content": {
                    "evidence_items": [
                        {
                            "title": "Finding A",
                            "statement": "Result A.",
                            "content_roles": ["finding"],
                        },
                        {
                            "title": "Finding B",
                            "statement": "Result B adds detail.",
                            "content_roles": ["finding"],
                        },
                    ]
                },
            },
            [
                {
                    "consumer_blocks": [
                        {
                            "block_id": "analysis",
                            "type": "narrative",
                            "content_role": "narrative",
                        }
                    ]
                }
            ],
        )

        items = normalized["block_content"]["analysis"]["items"]
        self.assertEqual([item["title"] for item in items], ["Finding B"])

    def test_structured_report_prunes_duplicate_or_empty_blocks_and_reflows(self):
        report = ReportAgent._finalize_structured_report(
            {
                "title": "Adaptive report",
                "summary": "Summary",
                "sections": [
                    {
                        "section_id": "analysis",
                        "layout": {"columns": 12},
                        "blocks": [
                            {
                                "block_id": "primary",
                                "type": "narrative",
                                "content_role": "executive_summary",
                                "layout": {"span": 8},
                                "status": "completed",
                                "content": {"text": "Distinct evidence."},
                            },
                            {
                                "block_id": "duplicate",
                                "type": "narrative",
                                "content_role": "narrative",
                                "layout": {"span": 4},
                                "status": "completed",
                                "content": {"text": "Distinct evidence."},
                            },
                            {
                                "block_id": "empty",
                                "type": "recommendations",
                                "layout": {"span": 12},
                                "status": "no_data",
                                "content": {"text": ""},
                            },
                        ],
                    }
                ],
            }
        )

        blocks = report["sections"][0]["blocks"]
        self.assertEqual([block["block_id"] for block in blocks], ["primary"])
        self.assertEqual(blocks[0]["layout"]["span"], 12)

    def test_structured_report_prunes_block_that_only_repeats_prior_claims(self):
        report = ReportAgent._finalize_structured_report(
            {
                "sections": [
                    {
                        "section_id": "findings",
                        "blocks": [
                            {
                                "block_id": "cards",
                                "type": "insight_grid",
                                "status": "completed",
                                "content": {
                                    "items": [
                                        {"title": "Finding A", "text": "Observed result A."},
                                        {"title": "Finding B", "text": "Observed result B."},
                                    ]
                                },
                            }
                        ],
                    },
                    {
                        "section_id": "repeated-analysis",
                        "blocks": [
                            {
                                "block_id": "repeated",
                                "type": "narrative",
                                "status": "completed",
                                "content": {
                                    "text": (
                                        "Finding A: Observed result A.\n"
                                        "Finding B: Observed result B."
                                    )
                                },
                            }
                        ],
                    },
                ]
            }
        )

        self.assertEqual(
            [section["section_id"] for section in report["sections"]],
            ["findings"],
        )

    def test_numeric_grounding_recalculates_declared_change_and_rejects_new_number(self):
        normalized, warnings = DataScienceProcessor._normalize_derived_metrics(
            [
                {
                    "value": 14250,
                    "comparison_value": 10200,
                    "change_percent": 40.2,
                }
            ]
        )
        self.assertEqual(normalized[0]["change_percent"], 39.7)
        self.assertTrue(warnings)

        grounded = DataScienceProcessor._ground_report_content(
            {
                "analysis_summary": "The measure improved by 54%.",
                "observations": [],
                "report_content": {
                    "executive_summary": "The measure improved by 54%.",
                    "key_findings": [
                        {"statement": "The valid change was 42.9%."},
                        {"statement": "The unsupported change was 54%."},
                    ],
                },
                "chart_data": {
                    "evidence_claim": "The unsupported improvement was 54%.",
                    "encoding": {"dimension": "period", "measures": ["value"]},
                    "rows": [
                        {"period": "A", "value": 1.82},
                        {"period": "B", "value": 1.04},
                    ],
                },
            },
            {
                "analysis_summary": "The value changed from 1.82 to 1.04 (42.9%).",
                "observations": [],
                "report_content": {
                    "executive_summary": "The value changed from 1.82 to 1.04 (42.9%).",
                    "key_findings": [],
                },
                "chart_data": {},
            },
            {"value": 1.04, "comparison_value": 1.82, "change_percent": -42.9},
        )

        self.assertNotIn("54", grounded["analysis_summary"])
        self.assertEqual(len(grounded["report_content"]["key_findings"]), 1)
        self.assertEqual(
            grounded["chart_data"]["evidence_claim"],
            "Across A to B, Value changed from 1.82 to 1.04.",
        )

    def test_chart_labels_follow_each_declared_measure(self):
        option = ChartAgent._align_option_to_dataset(
            {
                "yAxis": [{"type": "value"}, {"type": "value"}],
                "legend": {"data": ["Wrong", "Wrong"]},
                "series": [
                    {"name": "Wrong", "yAxisIndex": 0, "data": [1, 2]},
                    {"name": "Wrong", "yAxisIndex": 1, "data": [3, 4]},
                ],
            },
            {
                "datasets": [
                    {
                        "encoding": {"measures": ["measure_a", "measure_b"]},
                        "measures": [
                            {"field": "measure_a", "label": "Measure A", "unit": "u"},
                            {"field": "measure_b", "label": "Measure B", "unit": "v"},
                        ],
                        "data": [
                            {"period": "A", "measure_a": 1, "measure_b": 3},
                            {"period": "B", "measure_a": 2, "measure_b": 4},
                        ],
                    }
                ]
            },
        )

        self.assertEqual(
            [series["name"] for series in option["series"]],
            ["Measure A", "Measure B"],
        )
        self.assertEqual(
            [axis["name"] for axis in option["yAxis"]],
            ["Measure A (u)", "Measure B (v)"],
        )

    def test_chart_contract_drops_undeclared_series_and_grounds_claim(self):
        request = {
            "datasets": [
                {
                    "encoding": {
                        "dimension": "period",
                        "measures": ["measure_a", "measure_b", "measure_c"],
                    },
                    "measures": [
                        {"field": "measure_a", "label": "Measure A", "unit": "u"},
                        {"field": "measure_b", "label": "Measure B", "unit": "v"},
                        {"field": "measure_c", "label": "Measure C", "unit": "w"},
                    ],
                    "data": [
                        {
                            "period": "A",
                            "measure_a": 1,
                            "measure_b": 10,
                            "measure_c": 5,
                            "measure_d": 100,
                        },
                        {
                            "period": "B",
                            "measure_a": 2,
                            "measure_b": 8,
                            "measure_c": 5,
                            "measure_d": 200,
                        },
                    ],
                }
            ]
        }
        option = ChartAgent._align_option_to_dataset(
            {
                "title": {"text": "Untrusted four-series title"},
                "yAxis": [{"type": "value"}, {"type": "value"}],
                "legend": {"data": []},
                "series": [
                    {"name": "Wrong", "data": [1, 2]},
                    {"name": "Wrong", "data": [10, 8]},
                    {"name": "Wrong", "data": [5, 5]},
                    {"name": "Wrong", "data": [100, 200]},
                ],
                "grid": {},
            },
            request,
        )

        self.assertEqual(
            [series["name"] for series in option["series"]],
            ["Measure A", "Measure B", "Measure C"],
        )
        self.assertEqual(len(option["yAxis"]), 3)
        claim = ChartAgent._grounded_chart_claim(option, request)
        self.assertIn("Measure A (u) increased from 1 to 2", claim)
        self.assertIn("Measure B (v) decreased from 10 to 8", claim)
        self.assertIn("Measure C (w) remained stable at 5", claim)
        self.assertNotIn("200", claim)

    def test_report_finalization_prunes_repeated_items_and_rendered_warnings(self):
        report = ReportAgent._finalize_structured_report(
            {
                "sections": [
                    {
                        "layout": {"columns": 12},
                        "blocks": [
                            {
                                "type": "narrative",
                                "content": {
                                    "text": "Metric A: 1. Metric B: 2."
                                },
                            },
                            {
                                "type": "insight_grid",
                                "content": {
                                    "items": [
                                        {"title": "Metric A", "text": "1"},
                                        {"title": "Metric B", "text": "2"},
                                        {"title": "Metric C", "text": "3"},
                                    ]
                                },
                            },
                            {
                                "type": "recommendations",
                                "content": {"text": "Evidence is provisional."},
                            },
                        ],
                    }
                ],
                "warnings": ["Evidence is provisional."],
            }
        )

        items = report["sections"][0]["blocks"][1]["content"]["items"]
        self.assertEqual(items, [{"title": "Metric C", "text": "3"}])
        self.assertEqual(report["warnings"], [])

    def test_renderer_keeps_chart_claim_and_avoids_duplicate_hero_summary(self):
        rendered = ReportRenderer().render(
            {
                "title": "Evidence report",
                "summary": "The grounded executive answer.",
                "sections": [
                    {
                        "section_id": "overview",
                        "title": "Overview",
                        "blocks": [
                            {
                                "block_id": "summary",
                                "type": "narrative",
                                "content_role": "executive_summary",
                                "content": {"text": "The grounded executive answer."},
                            },
                            {
                                "block_id": "chart",
                                "type": "chart",
                                "content": {
                                    "chart_id": "adaptive.chart",
                                    "chart": {
                                        "selected_type": "line",
                                        "option": {
                                            "xAxis": {"type": "category"},
                                            "yAxis": {"type": "value"},
                                            "series": [{"type": "line", "data": [1, 2]}],
                                        },
                                    },
                                    "insight": {
                                        "claim": "The observed measure increased across the covered periods.",
                                        "purpose": "Compare the validated periods.",
                                        "coverage": "2 materialized records",
                                    },
                                },
                            },
                        ],
                    }
                ],
                "warnings": [],
            }
        )
        html = next(item["content"] for item in rendered if item["format"] == "html")

        self.assertNotIn('class="report-summary"', html)
        self.assertIn('class="chart-insight"', html)
        self.assertIn("The observed measure increased", html)
        self.assertIn("Why this chart", html)
        self.assertIn("2 materialized records", html)


if __name__ == "__main__":
    unittest.main()
