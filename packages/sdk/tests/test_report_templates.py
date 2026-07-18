import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report import (
    ChartInputAssembler,
    ChartAgent,
    PlanAgent,
    ReportAgent,
    ReportEngine,
    ReportRenderer,
    TemplateAgent,
    TemplatePool,
    _StepOutputRegistry,
    _json_structure,
    _negotiation_hash,
    _python_argument_name,
)


class ReportTemplateTests(unittest.TestCase):
    def setUp(self):
        self.pool = TemplatePool()
        self.agent = TemplateAgent(None, self.pool)

    def test_required_semantic_groups_must_all_be_satisfied(self):
        definition = self.pool.get("time-series-analysis")
        proposal = self.agent._materialize_instance(
            definition,
            {
                "steps": [
                    {
                        "step_id": "measure-only",
                        "outputs": [
                            {
                                "name": "values",
                                "shape": "time_series",
                                "semantic_roles": ["primary_measure"],
                            }
                        ],
                    }
                ]
            },
            None,
            "test",
        )

        self.assertEqual(proposal["status"], "needs_plan_revision")
        self.assertFalse(proposal["template_instance"]["bindings"])
        self.assertEqual(
            proposal["missing_data_requests"][0]["requirement_ref"],
            "time-series-values",
        )
        missing_by_requirement = {
            item["requirement_ref"]: item
            for item in proposal["missing_data_requests"]
        }
        self.assertFalse(missing_by_requirement["period-change"]["required"])
        self.assertFalse(missing_by_requirement["anomaly-candidates"]["required"])

    def test_every_manifest_template_can_be_loaded(self):
        loaded = {
            item["template_id"]: self.pool.get(item["template_id"])["name"]
            for item in self.pool.list_templates()
        }

        self.assertEqual(
            set(loaded),
            {
                "document-analysis",
                "data-profile",
                "executive-overview",
                "time-series-analysis",
                "segment-comparison",
            },
        )

    def test_source_format_selects_specialized_template(self):
        self.assertEqual(
            self.agent._source_template(
                DataCorpusPackage(sources=["C:\\uploads\\guide.pdf"])
            ),
            "document-analysis",
        )
        self.assertEqual(
            self.agent._source_template(
                DataCorpusPackage(sources=["C:\\uploads\\records.csv"])
            ),
            "data-profile",
        )

    def test_root_local_materialization_is_goal_evidence(self):
        normalized = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "read-file",
                        "description": "Materialize the selected source.",
                        "operation": {"kind": "inspect_data"},
                        "outputs": [
                            {
                                "name": "source-rows",
                                "shape": "table",
                                "semantic_roles": ["analysis_data"],
                            }
                        ],
                    }
                ]
            },
            ExecutionSpec(intent="report", objective="Analyze the file"),
            DataCorpusPackage(sources=["C:\\uploads\\source.pdf"]),
            None,
            [],
        )

        self.assertEqual(
            normalized["steps"][0]["outputs"][0]["semantic_roles"],
            ["analysis_data", "source_content", "goal_evidence"],
        )

    def test_optional_template_output_cannot_be_promoted_to_required(self):
        feedback = [
            {
                "request_id": "provide-headline",
                "requirement_ref": "headline-metrics",
                "required": False,
                "expected_output": {"shape": "record"},
                "semantic_roles": {"measures": ["headline_metric"]},
            }
        ]
        normalized = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "headline",
                        "required": True,
                        "operation": {"kind": "aggregate"},
                        "outputs": [
                            {
                                "name": "metrics",
                                "shape": "record",
                                "semantic_roles": ["headline_metric"],
                            }
                        ],
                    }
                ],
                "request_resolutions": [
                    {
                        "request_id": "provide-headline",
                        "decision": "added",
                        "output_refs": [
                            "step-output://headline/metrics"
                        ],
                    }
                ],
            },
            ExecutionSpec(intent="report", objective="Analyze the file"),
            DataCorpusPackage(),
            None,
            feedback,
        )

        self.assertFalse(normalized["steps"][0]["required"])

    def test_plan_shape_overrides_incompatible_generated_output_schema(self):
        schema = ReportEngine._step_output_schema(
            {
                "outputs": [
                    {"name": "rows", "shape": "table"}
                ]
            },
            {"output_schema": {"type": "object"}},
        )

        self.assertEqual(
            schema,
            {"type": "array", "items": {"type": "object"}},
        )

    def test_structured_report_rejects_repeated_generated_block_text(self):
        fallback = {
            "title": "Report",
            "summary": "Summary",
            "warnings": [],
            "sections": [
                {
                    "section_id": "overview",
                    "blocks": [
                        {
                            "block_id": "summary",
                            "type": "narrative",
                            "status": "completed",
                            "content": {"text": "Fallback summary."},
                        },
                        {
                            "block_id": "findings",
                            "type": "recommendations",
                            "status": "completed",
                            "content": {"text": "Distinct fallback findings."},
                        },
                    ],
                }
            ],
        }
        payload = {
            "sections": [
                {
                    "section_id": "overview",
                    "blocks": [
                        {
                            "block_id": "summary",
                            "type": "narrative",
                            "content": {"text": "Repeated generated text."},
                        },
                        {
                            "block_id": "findings",
                            "type": "recommendations",
                            "content": {"text": "Repeated generated text."},
                        },
                    ],
                }
            ]
        }

        aligned = ReportAgent._align_structured_payload(payload, fallback)

        blocks = aligned["sections"][0]["blocks"]
        self.assertEqual(
            blocks[0]["content"]["text"],
            "Repeated generated text.",
        )
        self.assertEqual(
            blocks[1]["content"]["text"],
            "Distinct fallback findings.",
        )

    def test_file_templates_require_goal_evidence_without_fixed_kpis(self):
        forbidden = {
            "document_unit_count",
            "total_character_count",
            "average_characters_per_unit",
            "truncated_unit_count",
        }
        for template_id in ("document-analysis", "data-profile"):
            definition = self.pool.get(template_id)
            requirements = {
                item["requirement_id"]: item
                for item in definition["data_requirements"]
            }
            self.assertTrue(requirements["goal-evidence"]["required"])
            serialized = str(definition)
            for field in forbidden:
                self.assertNotIn(field, serialized)

    def test_file_templates_build_overview_and_chart_from_goal_evidence(self):
        for template_id in ("document-analysis", "data-profile"):
            definition = self.pool.get(template_id)
            blocks = [
                block
                for section in definition["sections"]
                for block in section["blocks"]
            ]
            kpi = next(block for block in blocks if block["type"] == "kpi_group")
            chart = next(block for block in blocks if block["type"] == "chart")

            self.assertEqual(kpi["data_requirement_refs"], ["goal-evidence"])
            self.assertEqual(
                chart["chart_slot"]["data_requirement_refs"],
                ["goal-evidence"],
            )

    def test_optional_template_fallbacks_do_not_mark_report_partial(self):
        definition = self.pool.get("data-profile")
        proposal = self.agent._materialize_instance(
            definition,
            {
                "steps": [
                    {
                        "step_id": "read-content",
                        "outputs": [
                            {
                                "name": "content",
                                "shape": "table",
                                "semantic_roles": ["goal_evidence"],
                            }
                        ],
                    }
                ]
            },
            None,
            "test",
        )

        self.assertEqual(proposal["status"], "accepted")
        self.assertEqual(
            proposal["template_instance"]["status"],
            "accepted",
        )
        self.assertTrue(proposal["template_instance"]["applied_fallbacks"])

    def test_one_requirement_can_bind_multiple_plan_outputs(self):
        definition = self.pool.get("time-series-analysis")
        proposal = self.agent._materialize_instance(
            definition,
            {
                "steps": [
                    {
                        "step_id": "measure",
                        "outputs": [
                            {
                                "name": "values",
                                "shape": "time_series",
                                "semantic_roles": ["primary_measure"],
                            }
                        ],
                    },
                    {
                        "step_id": "calendar",
                        "outputs": [
                            {
                                "name": "periods",
                                "shape": "time_series",
                                "semantic_roles": ["time"],
                            }
                        ],
                    },
                ]
            },
            None,
            "test",
        )

        binding = next(
            item
            for item in proposal["template_instance"]["bindings"]
            if item["requirement_ref"] == "time-series-values"
        )
        self.assertEqual(
            binding["plan_output_refs"],
            [
                "step-output://measure/values",
                "step-output://calendar/periods",
            ],
        )

    def test_chart_input_assembler_collects_every_bound_output(self):
        chart_id = "template.section.chart"
        template_instance = {
            "bindings": [
                {
                    "requirement_ref": "comparison",
                    "status": "resolved",
                    "plan_output_refs": [
                        "step-output://step-a/a-values",
                        "step-output://step-b/b-values",
                    ],
                }
            ],
            "sections": [
                {
                    "blocks": [
                        {
                            "chart_slot": {
                                "chart_id": chart_id,
                                "intent": "Compare a and b",
                                "suggested_type": "line",
                                "allowed_types": ["line"],
                                "data_requirement_refs": ["comparison"],
                                "encoding": {},
                                "presentation": {},
                                "constraints": {"max_points": 10},
                                "fallback": {"action": "table"},
                            }
                        }
                    ]
                }
            ],
        }
        results = [
            self._step_result("step-a", "artifact://a", chart_id, "a", 10),
            self._step_result("step-b", "artifact://b", chart_id, "b", 20),
        ]

        ready, fallbacks = ChartInputAssembler().prepare(
            template_instance,
            results,
        )

        self.assertFalse(fallbacks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(len(ready[0]["datasets"]), 2)
        self.assertEqual(
            ready[0]["dataset_refs"],
            ["artifact://a", "artifact://b"],
        )

    def test_renderer_emits_css_javascript_and_standalone_chart_html(self):
        rendered = ReportRenderer().render(
            {
                "title": "A report",
                "summary": "Summary",
                "sections": [
                    {
                        "section_id": "overview",
                        "title": "Overview",
                        "blocks": [
                            {
                                "block_id": "chart",
                                "type": "chart",
                                "title": "A by period",
                                "content": {
                                    "chart_id": "report.overview.chart",
                                    "chart": {
                                        "option": {
                                            "xAxis": {"type": "category"},
                                            "yAxis": {"type": "value"},
                                            "series": [{"type": "line", "data": [1, 2]}],
                                        }
                                    },
                                },
                            }
                        ],
                    }
                ],
                "warnings": [],
            }
        )
        by_format = {item["format"]: item for item in rendered}

        self.assertEqual(
            set(by_format),
            {"markdown", "css", "javascript", "html"},
        )
        self.assertIn(".echarts-chart", by_format["css"]["content"])
        self.assertIn("window.echarts.init", by_format["javascript"]["content"])
        html = by_format["html"]["content"]
        self.assertIn("echarts@5.5.1", html)
        self.assertIn("<style>", html)
        self.assertIn('data-chart-id="report.overview.chart"', html)

    def test_renderer_uses_dashboard_layout_and_limits_kpis(self):
        metrics = [
            {"name": f"metric_{index}", "value": index}
            for index in range(1, 7)
        ]
        rendered = ReportRenderer().render(
            {
                "title": "Document report",
                "summary": "A short report.",
                "status": "completed",
                "template": {"template_id": "document-analysis"},
                "sections": [
                    {
                        "section_id": "snapshot",
                        "title": "Snapshot",
                        "purpose": "A visual overview.",
                        "layout": {"density": "compact"},
                        "blocks": [
                            {
                                "block_id": "metrics",
                                "type": "kpi_group",
                                "title": "At a glance",
                                "layout": {"span": 12, "emphasis": "featured"},
                                "content": {"metrics": metrics},
                            }
                        ],
                    }
                ],
                "sources": ["document.pdf"],
                "warnings": [],
            }
        )
        html = next(item["content"] for item in rendered if item["format"] == "html")

        self.assertIn('class="section-grid"', html)
        self.assertIn('style="--block-span:12"', html)
        self.assertEqual(html.count('class="kpi-item '), 4)
        self.assertNotIn("metric_5", html)
        self.assertIn(".report-block { grid-column: 1 / -1; }", html)

    def test_chart_fallback_maps_document_semantic_fields(self):
        chart = ChartAgent(None)._fallback_chart(
            {
                "chart_id": "document-density",
                "suggested_type": "bar",
                "allowed_types": ["bar"],
                "encoding_requirements": {
                    "x_role": "document_unit",
                    "y_roles": ["character_count"],
                },
                "presentation": {},
                "datasets": [
                    {
                        "dataset_id": "pages",
                        "artifact_ref": "artifact://pages",
                        "schema": {
                            "fields": [
                                {"name": "page_number", "type": "number"},
                                {"name": "character_count", "type": "number"},
                            ]
                        },
                        "data": [
                            {"page_number": 1, "character_count": 120},
                            {"page_number": 2, "character_count": 180},
                        ],
                    }
                ],
            }
        )

        self.assertEqual(
            chart["option"]["series"][0]["encode"],
            {"x": "page_number", "y": "character_count"},
        )

    def test_chart_polish_removes_invalid_formatter_and_dense_labels(self):
        option = ChartAgent._polish_option(
            {
                "xAxis": {"type": "category"},
                "yAxis": {
                    "type": "value",
                    "axisLabel": {"formatter": "{value|compactNumber}"},
                },
                "series": [
                    {
                        "type": "bar",
                        "data": list(range(35)),
                        "label": {"show": True},
                    }
                ],
            }
        )

        self.assertNotIn("formatter", option["yAxis"]["axisLabel"])
        self.assertFalse(option["series"][0]["label"]["show"])
        self.assertGreaterEqual(option["grid"]["bottom"], 58)

    def test_chart_polish_rotates_long_category_labels(self):
        option = ChartAgent._polish_option(
            {
                "xAxis": {
                    "type": "category",
                    "data": [
                        "Separation of concerns",
                        "Functional independence",
                    ],
                    "axisLabel": {"rotate": 0},
                },
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [5, 4]}],
            }
        )

        self.assertGreaterEqual(option["xAxis"]["axisLabel"]["rotate"], 28)
        self.assertGreaterEqual(option["grid"]["bottom"], 96)

    def test_structured_report_preserves_partial_template_status(self):
        report = ReportAgent(None)._fallback_structured(
            ExecutionSpec(intent="report", objective="A report"),
            {
                "instance_id": "instance",
                "template_id": "executive-overview",
                "template_version": "1.0.0",
                "revision": 1,
                "status": "partial",
                "sections": [],
            },
            [],
            [],
            {"sources": []},
        )

        self.assertEqual(report["status"], "partial")

    def test_report_blocks_use_distinct_analysis_content(self):
        result = {
            "step_id": "analyze",
            "analysis_summary": "Legacy summary.",
            "report_content": {
                "executive_summary": "Executive answer.",
                "key_findings": [
                    {"title": "Finding A", "statement": "Specific finding."}
                ],
                "supporting_evidence": [
                    {
                        "statement": "Concrete evidence.",
                        "source_location": "page 2",
                    }
                ],
                "implications": [
                    {"title": "Implication A", "statement": "Why it matters."}
                ],
                "limitations": ["The sample excludes appendix pages."],
            },
            "warnings": [],
        }

        self.assertEqual(
            ReportAgent._report_text_for_block(
                {"block_id": "executive-summary", "title": "Executive Summary"},
                [result],
                [],
            ),
            "Executive answer.",
        )
        self.assertEqual(
            ReportAgent._report_text_for_block(
                {"block_id": "key-findings", "title": "Key Findings"},
                [result],
                [],
            ),
            "Finding A: Specific finding.",
        )
        self.assertEqual(
            ReportAgent._report_text_for_block(
                {"block_id": "supporting-evidence", "title": "Supporting Evidence"},
                [result],
                [],
            ),
            "Concrete evidence. Source: page 2.",
        )
        self.assertEqual(
            ReportAgent._report_text_for_block(
                {"block_id": "analysis-interpretation", "title": "Interpretation"},
                [result],
                [],
            ),
            "Implication A: Why it matters.",
        )

    def test_report_metrics_preserve_analysis_order(self):
        metrics = ReportAgent(None)._collect_metrics(
            [
                {
                    "aggregated_metrics": [
                        {"name": "objective_score", "value": 92},
                        {"name": "page_count", "value": 35},
                        {
                            "name": "source_context",
                            "value": {"record_count": 35},
                        },
                    ]
                }
            ]
        )

        self.assertEqual(
            [item["name"] for item in metrics],
            ["objective_score", "page_count"],
        )

    def test_positive_coverage_notes_are_not_report_warnings(self):
        self.assertFalse(
            ReportAgent._is_material_warning(
                "All pages are represented; no null fields detected."
            )
        )
        self.assertFalse(
            ReportAgent._is_material_warning(
                "Unresolved template requirements: optional-chart"
            )
        )
        self.assertTrue(
            ReportAgent._is_material_warning(
                "Three source records were truncated."
            )
        )

    def test_negotiation_hash_ignores_revision_counters(self):
        first = _negotiation_hash(
            {"revision": 1, "steps": [{"step_id": "a"}]},
            {
                "template_instance": {
                    "revision": 1,
                    "template_id": "executive-overview",
                    "template_version": "1.0.0",
                    "bindings": [],
                    "applied_fallbacks": [],
                },
                "missing_data_requests": [],
            },
        )
        second = _negotiation_hash(
            {"revision": 2, "steps": [{"step_id": "a"}]},
            {
                "template_instance": {
                    "revision": 2,
                    "template_id": "executive-overview",
                    "template_version": "1.0.0",
                    "bindings": [],
                    "applied_fallbacks": [],
                },
                "missing_data_requests": [],
            },
        )

        self.assertEqual(first, second)

    def test_artifact_output_names_become_valid_python_arguments(self):
        self.assertEqual(
            _python_argument_name("aggregated_stats.json"),
            "aggregated_stats_json",
        )

    def test_single_list_field_is_materialized_as_table_rows(self):
        value = _StepOutputRegistry._output_value(
            {"document_pages": [{"page": 1}, {"page": 2}]},
            "generated-output-name",
            {"shape": "table"},
        )

        self.assertEqual(value, [{"page": 1}, {"page": 2}])
        self.assertEqual(
            _json_structure(value),
            {
                "type": "array",
                "item": {
                    "type": "object",
                    "fields": {"page": "number"},
                },
            },
        )

    def test_preflight_rejects_function_and_execution_argument_mismatch(self):
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "filter_rows",
                "source_code": "def filter_rows(rows):\n    return rows\n",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"rows_path": {"type": "string"}},
                    "required": ["rows_path"],
                },
            },
            {"rows_path": "/workspace/input.json"},
        )

        self.assertIn(
            "parameters_schema must declare required function argument: rows",
            errors,
        )
        self.assertIn(
            "Unexpected execution argument for generated function: rows_path",
            errors,
        )

    def test_generated_schema_is_aligned_to_function_signature(self):
        aligned = ReportEngine._align_generated_parameter_schema(
            {
                "tool_name": "build_density",
                "source_code": (
                    "def build_density(inspect_result_path: str) -> list[float]:\n"
                    "    return []\n"
                ),
                "parameters_schema": {
                    "type": "object",
                    "properties": {"inspect_result": {"type": "array"}},
                    "required": ["inspect_result"],
                },
                "execution_arguments": {"inspect_result": []},
            }
        )

        self.assertEqual(
            aligned["parameters_schema"],
            {
                "type": "object",
                "properties": {
                    "inspect_result_path": {"type": "string"},
                },
                "required": ["inspect_result_path"],
            },
        )
        self.assertEqual(aligned["execution_arguments"], {})

    @staticmethod
    def _step_result(step_id, artifact_ref, chart_id, field, value):
        return {
            "step_id": step_id,
            "chart_datasets": [
                {
                    "dataset_id": f"{step_id}-data",
                    "for_chart_ids": [chart_id],
                    "artifact_ref": artifact_ref,
                    "schema": {
                        "fields": [
                            {"name": "period", "type": "string"},
                            {"name": field, "type": "number"},
                        ]
                    },
                    "data": [{"period": "p1", field: value}],
                }
            ],
            "aggregated_metrics": [],
        }


if __name__ == "__main__":
    unittest.main()
