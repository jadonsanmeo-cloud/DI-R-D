import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report import (
    ChartInputAssembler,
    ChartAgent,
    PlanAgent,
    ReportAgent,
    ReportEngine,
    ReportPresentationPolicy,
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

    def test_instance_design_requires_analytical_development_not_fixed_layout(self):
        shallow_sections = [
            {
                "section_id": "opening",
                "blocks": [
                    {
                        "block_id": "answer",
                        "content_role": "executive_summary",
                        "required": True,
                    },
                    {
                        "block_id": "facts",
                        "content_role": "key_findings",
                        "required": True,
                    },
                ],
            }
        ]
        flexible_sections = [
            *shallow_sections,
            {
                "section_id": "any-run-local-shape",
                "blocks": [
                    {
                        "block_id": "development",
                        "content_role": "implication",
                        "required": True,
                    }
                ],
            },
        ]

        self.assertTrue(TemplateAgent._instance_design_issues(shallow_sections))
        self.assertEqual(
            TemplateAgent._instance_design_issues(flexible_sections),
            [],
        )

    def test_markdown_boundary_resolves_and_persists_presentation_roles(self):
        class ContractLLM:
            def __init__(self):
                self.calls = 0

            def invoke(self, prompt):
                self.calls += 1
                rendered = prompt.to_string()
                if "resolve_requested_content_roles" in rendered:
                    return SimpleNamespace(
                        content=json.dumps(
                            {
                                "requested_content_roles": [
                                    "recommendation",
                                    "chart",
                                ]
                            }
                        )
                    )
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "template_id": "business-economics-finance",
                            "confidence": 0.95,
                            "selection_reason": (
                                "The objective concerns operational evidence and "
                                "decision support rather than a narrow time series."
                            ),
                            "requested_content_roles": [],
                        }
                    )
                )

        llm = ContractLLM()
        proposal = TemplateAgent(llm, self.pool).run(
            ExecutionSpec(
                intent="report",
                objective="Assess the available operational evidence",
                constraints={
                    "confirmed_spec_markdown": (
                        "# Interactive Execution Spec\n\n"
                        "## Expected Output\n\n"
                        "Include explicit actions and an evidence-backed visual."
                    )
                },
            ),
            {"steps": []},
            DataCorpusPackage(),
        )

        provenance = proposal["template_instance"]["provenance"]
        self.assertEqual(
            provenance["requested_content_roles"],
            ["chart", "recommendation"],
        )
        blocks = [
            block
            for section in proposal["template_instance"]["sections"]
            for block in section.get("blocks", [])
        ]
        self.assertTrue(
            next(
                block
                for block in blocks
                if block.get("content_role") == "recommendation"
            )["required"]
        )
        self.assertFalse(
            next(
                block
                for block in blocks
                if block.get("content_role") == "chart"
            )["required"]
        )
        self.assertEqual(llm.calls, 2)

    def test_requested_chart_role_is_restored_after_blueprint_adaptation(self):
        definition = self.pool.get("business-economics-finance")
        sections_without_chart = [
            {
                **section,
                "blocks": [
                    block
                    for block in section.get("blocks", [])
                    if block.get("content_role") != "chart"
                ],
            }
            for section in definition.get("sections", [])
            if any(
                block.get("content_role") != "chart"
                for block in section.get("blocks", [])
            )
        ]
        spec = ExecutionSpec(
            intent="report",
            objective="Create an operational report",
            constraints={
                "output_requirements": {
                    "content_roles": ["chart", "recommendation"],
                },
            },
        )

        restored = TemplateAgent._ensure_requested_content_roles(
            sections_without_chart,
            definition,
            spec,
        )

        self.assertTrue(
            any(
                block.get("content_role") == "chart"
                for section in restored
                for block in section.get("blocks", [])
            )
        )
        self.assertTrue(
            any(
                block.get("content_role") == "recommendation"
                for section in restored
                for block in section.get("blocks", [])
            )
        )
        recommendation = next(
            block
            for section in restored
            for block in section.get("blocks", [])
            if block.get("content_role") == "recommendation"
        )
        chart = next(
            block
            for section in restored
            for block in section.get("blocks", [])
            if block.get("content_role") == "chart"
        )
        self.assertTrue(recommendation["required"])
        self.assertFalse(chart["required"])

    def test_requested_roles_are_not_inferred_from_objective_keywords(self):
        definition = self.pool.get("business-economics-finance")
        sections = [
            {
                **section,
                "blocks": [
                    block
                    for block in section.get("blocks", [])
                    if block.get("content_role") != "chart"
                ],
            }
            for section in definition.get("sections", [])
        ]

        unchanged = TemplateAgent._ensure_requested_content_roles(
            sections,
            definition,
            ExecutionSpec(
                intent="report",
                objective="Create a chart and visualize the result",
            ),
        )

        self.assertFalse(
            any(
                block.get("content_role") == "chart"
                for section in unchanged
                for block in section.get("blocks", [])
            )
        )

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
            item["requirement_ref"]: item for item in proposal["missing_data_requests"]
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
                "adaptive-raw-report",
                "business-economics-finance",
                "document-analysis",
                "data-profile",
                "education-learning",
                "executive-overview",
                "health-wellbeing",
                "media-arts-entertainment",
                "science-policy-environment",
                "time-series-analysis",
                "segment-comparison",
                "society-culture-relationships",
                "technology-engineering",
            },
        )

    def test_domain_templates_inherit_adaptive_structure_from_pool(self):
        definition = self.pool.get("technology-engineering")

        self.assertEqual(definition["template_id"], "technology-engineering")
        self.assertTrue(definition["sections"])
        self.assertEqual(
            definition["data_requirements"][0]["requirement_id"],
            "goal-evidence",
        )
        self.assertIn(
            "quality attributes",
            " ".join(definition["adaptation"]["guidance"]),
        )

    def test_llm_receives_domain_and_cross_domain_architecture_candidates(self):
        candidate_ids = {
            item["template_id"]
            for item in self.pool.selection_candidates()
        }

        self.assertEqual(
            candidate_ids,
            {
                "business-economics-finance",
                "education-learning",
                "health-wellbeing",
                "media-arts-entertainment",
                "science-policy-environment",
                "society-culture-relationships",
                "technology-engineering",
                "document-analysis",
                "data-profile",
                "executive-overview",
                "time-series-analysis",
                "segment-comparison",
            },
        )
        self.assertNotIn("adaptive-raw-report", candidate_ids)
        self.assertIn("document-analysis", candidate_ids)

    def test_low_confidence_selection_uses_manifest_raw_fallback(self):
        proposal = self.agent.run(
            ExecutionSpec(intent="report", objective="Analyze this source"),
            {"steps": []},
            DataCorpusPackage(sources=["unknown.bin"]),
        )

        self.assertEqual(
            proposal["selection"]["template_id"],
            self.pool.manifest()["fallback_template_id"],
        )
        self.assertEqual(proposal["selection"]["confidence"], 0.0)

    def test_llm_selection_builds_valid_dynamic_instance(self):
        class RecordingLLM:
            def __init__(self):
                self.prompt = ""

            def invoke(self, prompt):
                self.prompt = prompt.to_string()
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "template_id": "technology-engineering",
                            "confidence": 0.93,
                            "selection_reason": (
                                "The content describes software architecture, "
                                "modularity, dependencies, and trade-offs."
                            ),
                            "content_profile": {
                                "domain": "technology and engineering"
                            },
                            "title_strategy": (
                                "Name the software-design subject and central tension."
                            ),
                            "instance_blueprint": {
                                "sections": [
                                    {
                                        "section_id": "design-overview",
                                        "title": "Design Concepts at a Glance",
                                        "purpose": "Profile and summarize the material.",
                                        "required": True,
                                        "layout": {
                                            "columns": 12,
                                            "density": "comfortable",
                                        },
                                        "blocks": [
                                            {
                                                "content_role": "data_profile",
                                                "block_id": "profile",
                                            },
                                            {
                                                "content_role": "executive_summary",
                                                "block_id": "summary",
                                            },
                                            {
                                                "content_role": "key_findings",
                                                "block_id": "findings",
                                            },
                                        ],
                                    },
                                    {
                                        "section_id": "evidence",
                                        "title": "Architectural Evidence",
                                        "purpose": "Ground the interpretation.",
                                        "required": True,
                                        "layout": {
                                            "columns": 12,
                                            "density": "detailed",
                                        },
                                        "blocks": [
                                            {
                                                "content_role": "supporting_evidence",
                                                "block_id": "evidence-trail",
                                            },
                                            {
                                                "content_role": "implication",
                                                "block_id": "architectural-interpretation",
                                                "required": True,
                                            }
                                        ],
                                    },
                                    {
                                        "section_id": "limits",
                                        "title": "Synthesis and Limits",
                                        "purpose": "Conclude with evidence limits.",
                                        "required": True,
                                        "layout": {
                                            "columns": 12,
                                            "density": "detailed",
                                        },
                                        "blocks": [
                                            {
                                                "content_role": "limitation",
                                                "block_id": "limitations",
                                            }
                                        ],
                                    },
                                ]
                            },
                        }
                    )
                )

        llm = RecordingLLM()
        agent = TemplateAgent(llm, self.pool)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "software-design.md"
            source.write_text(
                "Unique preview marker: modular boundaries and information hiding.",
                encoding="utf-8",
            )
            proposal = agent.run(
                ExecutionSpec(
                    intent="report",
                    objective="Explain the software design concepts",
                ),
                {
                    "steps": [
                        {
                            "step_id": "read",
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
                DataCorpusPackage(sources=[str(source)]),
            )

        self.assertEqual(
            proposal["selection"]["template_id"],
            "technology-engineering",
        )
        self.assertEqual(len(proposal["template_instance"]["sections"]), 3)
        self.assertEqual(
            proposal["template_instance"]["sections"][0]["title"],
            "Design Concepts at a Glance",
        )
        self.assertIn("Education and Learning", llm.prompt)
        self.assertIn("Technology and Engineering", llm.prompt)
        self.assertIn("Unique preview marker", llm.prompt)
        self.assertIn("technical-profile", llm.prompt)
        self.assertIn("archetype_ref", llm.prompt)
        self.assertNotIn('"name": "Adaptive Raw Report"', llm.prompt)
        self.assertIn('"name": "Document Analysis"', llm.prompt)
        self.assertEqual(
            proposal["selection"]["mode"],
            "llm",
        )
        self.assertEqual(
            proposal["template_instance"]["provenance"]["design_source"],
            "llm_blueprint",
        )

    def test_architecture_candidates_publish_legal_archetypes(self):
        candidates = self.pool.selection_candidates()

        self.assertTrue(candidates)
        role_sets = []
        for candidate in candidates:
            sections = candidate.get("section_archetypes", [])
            self.assertGreaterEqual(len(sections), 1)
            blocks = [
                block
                for section in sections
                for block in section.get("blocks", [])
            ]
            self.assertTrue(blocks)
            self.assertTrue(all(block.get("archetype_ref") for block in blocks))
            self.assertTrue(all(block.get("content_role") for block in blocks))
            role_sets.append(
                frozenset(str(block.get("content_role")) for block in blocks)
            )

        self.assertGreater(len(set(role_sets)), 1)

    def test_archetype_ref_preserves_guardrails_and_contract_requiredness(self):
        definition = self.pool.get("technology-engineering")
        sections = self.agent._adapt_sections(
            definition,
            {
                "instance_blueprint": {
                    "sections": [
                        {
                            "section_id": "opening",
                            "title": "Opening",
                            "purpose": "Answer the technical question.",
                            "required": False,
                            "blocks": [
                                {
                                    "archetype_ref": "technical-profile",
                                    "content_role": "data_profile",
                                    "block_id": "profile",
                                    "required": False,
                                    "instructions": ["Focus on the selected runtime."],
                                },
                                {
                                    "archetype_ref": "technical-summary",
                                    "content_role": "executive_summary",
                                    "block_id": "summary",
                                    "required": False,
                                },
                                {
                                    "archetype_ref": "engineering-findings",
                                    "content_role": "key_findings",
                                    "block_id": "findings",
                                    "required": False,
                                },
                            ],
                        },
                        {
                            "section_id": "evidence",
                            "title": "Evidence",
                            "purpose": "Ground the answer.",
                            "required": False,
                            "blocks": [
                                {
                                    "archetype_ref": "technical-evidence-trail",
                                    "content_role": "supporting_evidence",
                                    "block_id": "evidence-list",
                                    "required": False,
                                }
                            ],
                        },
                        {
                            "section_id": "limits",
                            "title": "Limits",
                            "purpose": "Bound the conclusion.",
                            "required": False,
                            "blocks": [
                                {
                                    "archetype_ref": "technical-limitations-list",
                                    "content_role": "limitation",
                                    "block_id": "limitations",
                                    "required": False,
                                }
                            ],
                        },
                    ]
                }
            },
        )

        profile = sections[0]["blocks"][0]
        self.assertEqual(profile["type"], "profile")
        self.assertFalse(profile["required"])
        self.assertTrue(sections[0]["required"])
        self.assertIn("Focus on the selected runtime.", profile["instructions"])
        self.assertTrue(
            any(
                "system boundary" in instruction
                for instruction in profile["instructions"]
            )
        )

    def test_explicit_template_rejects_different_domain_blueprint(self):
        class DifferentDomainLLM:
            def invoke(self, prompt):
                del prompt
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "template_id": "education-learning",
                            "confidence": 0.98,
                            "selection_reason": "Education vocabulary was present.",
                            "instance_blueprint": {
                                "sections": [
                                    {
                                        "section_id": "wrong-domain",
                                        "title": "Wrong Domain",
                                        "purpose": "This must not be applied.",
                                        "required": True,
                                        "blocks": [
                                            {
                                                "archetype_ref": "learning-profile",
                                                "content_role": "data_profile",
                                                "block_id": "wrong-profile",
                                            }
                                        ],
                                    }
                                ]
                            },
                        }
                    )
                )

        agent = TemplateAgent(DifferentDomainLLM(), self.pool)
        proposal = agent.run(
            ExecutionSpec(
                intent="report",
                objective="Review the service architecture",
                constraints={"template_id": "technology-engineering"},
            ),
            {
                "steps": [
                    {
                        "step_id": "read",
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
            DataCorpusPackage(),
        )

        instance = proposal["template_instance"]
        self.assertEqual(instance["sections"][0]["section_id"], "system-orientation")
        self.assertEqual(instance["provenance"]["selection_mode"], "explicit")
        self.assertEqual(instance["provenance"]["design_source"], "canonical_template")

    def test_revised_plan_can_change_a_non_explicit_template_selection(self):
        class RevisedSelectionLLM:
            def invoke(self, prompt):
                del prompt
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "template_id": "technology-engineering",
                            "confidence": 0.96,
                            "selection_reason": (
                                "The validated plan now exposes system-boundary "
                                "and implementation evidence."
                            ),
                        }
                    )
                )

        previous_instance = {
            "template_id": "education-learning",
            "revision": 1,
            "status": "draft",
            "provenance": {
                "selection_mode": "deterministic_fallback",
                "selection_confidence": 0.0,
            },
        }
        proposal = TemplateAgent(RevisedSelectionLLM(), self.pool).run(
            ExecutionSpec(
                intent="report",
                objective="Assess the service architecture",
            ),
            {
                "steps": [
                    {
                        "step_id": "architecture-evidence",
                        "outputs": [
                            {
                                "name": "system-evidence",
                                "shape": "table",
                                "semantic_roles": ["goal_evidence"],
                            }
                        ],
                    }
                ]
            },
            DataCorpusPackage(),
            previous_instance,
        )

        self.assertEqual(
            proposal["selection"]["template_id"],
            "technology-engineering",
        )
        self.assertEqual(proposal["selection"]["mode"], "llm_revision")

    def test_llm_selection_without_blueprint_uses_neutral_architecture(self):
        class SelectionOnlyLLM:
            def invoke(self, prompt):
                del prompt
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "template_id": "technology-engineering",
                            "confidence": 0.94,
                            "selection_reason": (
                                "The objective and source concern service boundaries "
                                "and architectural trade-offs."
                            ),
                        }
                    )
                )

        proposal = TemplateAgent(SelectionOnlyLLM(), self.pool).run(
            ExecutionSpec(
                intent="report",
                objective="Assess service boundaries and architectural trade-offs",
            ),
            {
                "steps": [
                    {
                        "step_id": "analyze",
                        "outputs": [
                            {
                                "name": "evidence",
                                "shape": "table",
                                "semantic_roles": ["goal_evidence"],
                            }
                        ],
                    }
                ]
            },
            DataCorpusPackage(),
        )

        instance = proposal["template_instance"]
        self.assertEqual(instance["template_id"], "technology-engineering")
        self.assertEqual(
            instance["provenance"]["design_source"],
            "adaptive_fallback",
        )
        self.assertEqual(
            [section["section_id"] for section in instance["sections"]],
            [
                section["section_id"]
                for section in self.pool.get("adaptive-raw-report")["sections"]
            ],
        )

    def test_dynamic_instance_normalizes_invalid_section_layouts(self):
        definition = self.pool.get("adaptive-raw-report")
        requested_sections = json.loads(json.dumps(definition["sections"]))
        requested_sections[0]["layout"] = "comfortable"
        requested_sections[1]["layout"] = None
        requested_sections[2]["layout"] = {
            "columns": 99,
            "density": "unexpected",
        }

        sections = self.agent._adapt_sections(
            definition,
            {"instance_blueprint": {"sections": requested_sections}},
        )

        self.assertEqual(
            sections[0]["layout"],
            {"columns": 12, "density": "comfortable"},
        )
        self.assertEqual(
            sections[1]["layout"],
            {"columns": 12, "density": "comfortable"},
        )
        self.assertEqual(
            sections[2]["layout"],
            {"columns": 12, "density": "comfortable"},
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

    def test_root_local_materialization_is_source_content_only(self):
        normalized = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "read-file",
                        "description": "Materialize the selected source.",
                        "operation": {"kind": "read_source_content"},
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
            ["analysis_data", "source_content"],
        )

    def test_singular_semantic_role_is_preserved_and_binds_template(self):
        normalized = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "materialize-evidence",
                        "description": "Materialize objective-relevant evidence.",
                        "operation": {"kind": "retrieve"},
                        "outputs": [
                            {
                                "name": "evidence",
                                "shape": "table",
                                "semantic_role": "goal_evidence",
                            }
                        ],
                    }
                ]
            },
            ExecutionSpec(intent="report", objective="Analyze ingested evidence"),
            DataCorpusPackage(),
            None,
            [],
        )
        output = normalized["steps"][0]["outputs"][0]
        proposal = self.agent._materialize_instance(
            self.pool.get("adaptive-raw-report"),
            normalized,
            None,
            "test",
        )

        self.assertIn("goal_evidence", output["semantic_roles"])
        self.assertEqual(proposal["status"], "accepted")
        self.assertEqual(
            proposal["template_instance"]["bindings"][0]["plan_output_ref"],
            "step-output://materialize-evidence/evidence",
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
                        "output_refs": ["step-output://headline/metrics"],
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
            {"outputs": [{"name": "rows", "shape": "table"}]},
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

    def test_structured_summary_uses_dedicated_executive_summary_block(self):
        fallback = {
            "title": "Operations report",
            "summary": "Fallback joined every step summary.",
            "warnings": [],
            "sections": [
                {
                    "section_id": "overview",
                    "blocks": [
                        {
                            "block_id": "executive-summary",
                            "type": "narrative",
                            "content_role": "executive_summary",
                            "status": "completed",
                            "content": {"text": "Grounded executive answer."},
                        }
                    ],
                }
            ],
        }
        payload = {
            "summary": "Unbounded concatenation from every analysis step.",
            "sections": [],
        }

        aligned = ReportAgent._align_structured_payload(payload, fallback)

        self.assertEqual(aligned["summary"], "Grounded executive answer.")

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
                item["requirement_id"]: item for item in definition["data_requirements"]
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
                                            "series": [
                                                {"type": "line", "data": [1, 2]}
                                            ],
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
        self.assertIn('class="report-nav"', html)
        self.assertIn('class="theme-toggle"', html)
        self.assertIn("Document profile", html)

    def test_renderer_uses_dashboard_layout_and_configured_kpi_limit(self):
        metrics = [{"name": f"metric_{index}", "value": index} for index in range(1, 7)]
        rendered = ReportRenderer(
            presentation_policy=ReportPresentationPolicy(max_kpi_items=4)
        ).render(
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

    def test_renderer_falls_back_for_invalid_layout_types(self):
        rendered = ReportRenderer().render(
            {
                "title": "Layout fallback report",
                "summary": "Renderer input contains malformed layout values.",
                "sections": [
                    {
                        "section_id": "string-layout",
                        "title": "String layout",
                        "layout": "comfortable",
                        "blocks": [
                            {
                                "block_id": "string-block-layout",
                                "type": "narrative",
                                "title": "Narrative",
                                "layout": "featured",
                                "content": {"text": "Valid report content."},
                            }
                        ],
                    },
                    {
                        "section_id": "null-layout",
                        "title": "Null layout",
                        "layout": None,
                        "blocks": [
                            {
                                "block_id": "null-block-layout",
                                "type": "narrative",
                                "title": "Narrative",
                                "layout": None,
                                "content": {"text": "More valid report content."},
                            }
                        ],
                    },
                ],
                "warnings": [],
            }
        )
        html = next(item["content"] for item in rendered if item["format"] == "html")

        self.assertEqual(html.count("density-comfortable"), 2)
        self.assertIn('style="--block-span:6"', html)

    def test_renderer_omits_empty_optional_table_without_material_issue(self):
        rendered = ReportRenderer().render(
            {
                "title": "Evidence report",
                "summary": "A meaningful summary.",
                "sections": [
                    {
                        "section_id": "quality",
                        "title": "Quality",
                        "blocks": [
                            {
                                "block_id": "optional-table",
                                "type": "table",
                                "required": False,
                                "status": "no_data",
                                "content": {"rows": []},
                            }
                        ],
                    }
                ],
                "warnings": [],
            }
        )
        html = next(item["content"] for item in rendered if item["format"] == "html")

        self.assertNotIn("No table data is available", html)
        self.assertNotIn("Material Data Issues", html)

    def test_renderer_supports_profile_and_non_chart_visual_blocks(self):
        rendered = ReportRenderer().render(
            {
                "title": "Software design concepts",
                "summary": "A detailed subject-specific summary.",
                "sections": [
                    {
                        "section_id": "overview",
                        "title": "Overview",
                        "blocks": [
                            {
                                "block_id": "profile",
                                "type": "profile",
                                "content": {
                                    "items": [
                                        {"label": "File type", "value": "HTML"}
                                    ]
                                },
                            },
                            {
                                "block_id": "insights",
                                "type": "insight_grid",
                                "content": {
                                    "items": [
                                        {
                                            "title": "Modularity",
                                            "text": "Boundaries reduce change propagation.",
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                ],
                "warnings": [],
            }
        )
        html = next(item["content"] for item in rendered if item["format"] == "html")

        self.assertIn('class="profile-list"', html)
        self.assertIn('class="insight-grid"', html)
        self.assertIn("01 / Analysis", html)

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

    def test_chart_option_labels_are_aligned_to_dataset_semantics(self):
        option = ChartAgent._align_option_to_dataset(
            {
                "title": {"text": "Wrong title"},
                "yAxis": {"type": "value", "name": "Wrong unit"},
                "series": [{"name": "Wrong measure", "data": [1, 2]}],
            },
            {
                "datasets": [
                    {
                        "title": "Monthly transaction volume",
                        "measure": "Transactions",
                        "unit": "thousands",
                        "data": [
                            {"category": "Jan", "value": 10.2},
                            {"category": "Feb", "value": 10.8},
                        ],
                    }
                ]
            },
        )

        self.assertEqual(option["title"]["text"], "Monthly transaction volume")
        self.assertEqual(option["yAxis"]["name"], "Transactions (thousands)")
        self.assertEqual(option["series"][0]["name"], "Transactions")

    def test_chart_measure_label_does_not_repeat_embedded_unit(self):
        self.assertEqual(
            ChartAgent._measure_label(
                {"measure": "Revenue (USD)", "unit": "USD"}
            ),
            "Revenue (USD)",
        )

    def test_visual_evidence_does_not_expose_internal_artifact_uri(self):
        items = ReportAgent._visual_items_for_block(
            {"content_role": "supporting_evidence"},
            [
                {
                    "report_content": {
                        "supporting_evidence": [
                            {
                                "statement": "Monthly rows support the trend.",
                                "source_location": "artifact://run/data.json#sample",
                            }
                        ]
                    }
                }
            ],
        )

        self.assertEqual(items, [{"title": "", "text": "Monthly rows support the trend."}])

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

    def test_structured_report_repairs_missing_and_shallow_required_narratives(self):
        executive_text = (
            "The observed result improved across the covered period, while the "
            "available evidence links that change to documented operating actions. "
            "The decision implication is to preserve the validated controls while "
            "monitoring whether the result holds beyond the bounded evidence window."
        )
        analysis_text = (
            "The evidence shows a sustained directional pattern rather than an "
            "isolated endpoint. The materialized observations provide the comparison "
            "basis, and the documented interventions offer plausible operating context. "
            "Those interventions remain explanatory hypotheses unless the source "
            "establishes attribution. The practical consequence is to separate the "
            "observed performance signal from the mechanism proposed by the source, "
            "then validate both against additional periods and comparable conditions."
        )

        class RepairingReportAgent(ReportAgent):
            def __init__(self):
                super().__init__(object())
                self.calls = []

            def _invoke_json_with_prompt(self, system_prompt, **inputs):
                del system_prompt
                self.calls.append(inputs)
                if len(self.calls) == 1:
                    return {
                        "title": "Evidence Review",
                        "sections": [
                            {
                                "section_id": "overview",
                                "blocks": [
                                    {
                                        "block_id": "executive",
                                        "type": "narrative",
                                        "content": {"text": "Metrics improved."},
                                    }
                                ],
                            }
                        ],
                    }
                return {
                    "title": "Evidence Review",
                    "sections": [
                        {
                            "section_id": "overview",
                            "blocks": [
                                {
                                    "block_id": "executive",
                                    "type": "narrative",
                                    "content": {"text": executive_text},
                                },
                                {
                                    "block_id": "deep-analysis",
                                    "type": "narrative",
                                    "content": {"text": analysis_text},
                                },
                            ],
                        }
                    ],
                }

        instance = {
            "instance_id": "instance",
            "template_id": "adaptive-raw-report",
            "template_version": "1.0.0",
            "revision": 1,
            "status": "accepted",
            "bindings": [],
            "sections": [
                {
                    "section_id": "overview",
                    "title": "Overview",
                    "purpose": "Answer and analyze the evidence.",
                    "layout": {"columns": 12},
                    "blocks": [
                        {
                            "block_id": "executive",
                            "type": "narrative",
                            "content_role": "executive_summary",
                            "title": "Executive Answer",
                            "required": True,
                            "layout": {"span": 12},
                            "instructions": ["Synthesize the decision answer."],
                            "data_requirement_refs": [],
                        },
                        {
                            "block_id": "deep-analysis",
                            "type": "narrative",
                            "content_role": "narrative",
                            "title": "Deep Analysis",
                            "required": True,
                            "layout": {"span": 12},
                            "instructions": ["Develop evidence and interpretation."],
                            "data_requirement_refs": [],
                        },
                    ],
                }
            ],
        }
        agent = RepairingReportAgent()
        report = agent.run_structured(
            ExecutionSpec(intent="report", objective="Analyze the evidence"),
            instance,
            [],
            [],
            {"sources": []},
        )

        blocks = report["sections"][0]["blocks"]
        self.assertEqual(len(agent.calls), 2)
        self.assertEqual([block["block_id"] for block in blocks], ["executive", "deep-analysis"])
        self.assertEqual(blocks[0]["content"]["text"], executive_text)
        self.assertEqual(blocks[1]["content"]["text"], analysis_text)
        repair_ids = {
            block["block_id"]
            for section in agent.calls[1]["template_instance"]["sections"]
            for block in section["blocks"]
        }
        self.assertEqual(repair_ids, {"executive", "deep-analysis"})

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
            ReportAgent._is_material_warning("Three source records were truncated.")
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

    def test_generated_schema_mismatch_is_rejected_instead_of_aligned(self):
        errors = ReportEngine._execution_argument_errors(
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
                "output_schema": {"type": "array", "items": {"type": "number"}},
                "execution_arguments": {"inspect_result": []},
            },
            {"inspect_result": []},
        )

        self.assertIn(
            "parameters_schema must declare required function argument: "
            "inspect_result_path",
            errors,
        )
        self.assertIn(
            "parameters_schema declares argument absent from generated function: "
            "inspect_result",
            errors,
        )

    def test_generated_code_rejects_corpus_uri_as_local_path(self):
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "load_rows",
                "source_code": "def load_rows(source_path: str):\n    return []\n",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"source_path": {"type": "string"}},
                    "required": ["source_path"],
                },
                "output_schema": {"type": "array"},
            },
            {"source_path": "corpus://organization/document"},
        )

        self.assertIn("requires a sandbox artifact path", "; ".join(errors))

    def test_generated_code_rejects_duplicate_parameters_for_one_input(self):
        errors = ReportEngine._execution_argument_errors(
            {
                "tool_name": "calculate",
                "source_code": (
                    "def calculate(rows, rows_path):\n"
                    "    return len(rows)\n"
                ),
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array"},
                        "rows_path": {"type": "string"},
                    },
                    "required": ["rows", "rows_path"],
                },
                "output_schema": {"type": "integer"},
                "argument_bindings": {
                    "rows": {
                        "input_ref": "step-output://load/rows",
                        "adapter": "identity",
                    },
                    "rows_path": {
                        "input_ref": "step-output://load/rows",
                        "adapter": "artifact_path",
                    },
                },
            },
            {"rows": [], "rows_path": "/workspace/input/rows.json"},
        )

        self.assertIn("exactly one parameter per resolved input", "; ".join(errors))

    def test_generated_output_uses_full_nested_json_schema_validation(self):
        errors = ReportEngine._output_contract_errors(
            {"series": [{"label": "January", "value": "not-a-number"}]},
            {
                "type": "object",
                "properties": {
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "number"},
                            },
                            "required": ["label", "value"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["series"],
            },
        )

        self.assertIn("series.0.value", "; ".join(errors))

    def test_chart_fallback_distinguishes_empty_output_from_missing_output(self):
        chart_id = "profile.primary.chart"
        template_instance = {
            "bindings": [
                {
                    "requirement_ref": "comparison",
                    "status": "resolved",
                    "plan_output_refs": ["step-output://load/rows"],
                }
            ],
            "sections": [
                {
                    "blocks": [
                        {
                            "chart_slot": {
                                "chart_id": chart_id,
                                "suggested_type": "bar",
                                "data_requirement_refs": ["comparison"],
                                "fallback": {"action": "omit"},
                            }
                        }
                    ]
                }
            ],
        }
        result = self._step_result("load", "artifact://rows", chart_id, "value", 1)
        result["chart_datasets"][0]["data"] = []

        ready, fallbacks = ChartInputAssembler().prepare(
            template_instance,
            [result],
        )

        self.assertFalse(ready)
        self.assertEqual(len(fallbacks), 1)
        self.assertIn("no chartable rows", fallbacks[0]["warnings"][0])
        self.assertNotIn("Unavailable plan outputs", fallbacks[0]["warnings"][0])

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
