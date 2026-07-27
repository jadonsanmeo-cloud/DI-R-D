from __future__ import annotations

import unittest

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    ExecutionSpec,
    IntentAnalysis,
    PreprocessingStep,
)
from data_intelligence_sdk.spec.markdown import render_spec_markdown


class SpecMarkdownTests(unittest.TestCase):
    def test_renders_stable_human_readable_spec(self) -> None:
        step = PreprocessingStep(
            name="resolve_request_context",
            order=3,
            step_type="resolve_context",
            description="Resolve requested metrics.",
            capability="metric_resolution",
            required=True,
            depends_on=["understand_request"],
        )
        spec = ExecutionSpec(
            intent="reason",
            objective="Calculate order totals.",
            data_requirements=["orders.csv"],
            preprocessing_steps=[step],
            capability_requirements=[
                CapabilityRequirement(
                    name="aggregate_data",
                    description="Aggregate order values.",
                    input_schema={"source": "string"},
                    constraints={"allowed": {"metrics": ["total"]}},
                )
            ],
            constraints={"language": "en", "scope": {"tables": ["orders"]}},
            engine_hint="general",
        )
        analysis = IntentAnalysis(
            intent="reason",
            catalog_intent_id="data_query",
            preprocessing_steps=[step],
        )

        markdown = render_spec_markdown(spec, analysis)

        self.assertIn("# Execution Spec (Draft)", markdown)
        self.assertIn("## Objective\n\nCalculate order totals.", markdown)
        self.assertIn("- Normalized: `reason`", markdown)
        self.assertIn("- Catalog: `data_query`", markdown)
        self.assertIn("1. **resolve_request_context** (`resolve_context`)", markdown)
        self.assertIn("### aggregate_data", markdown)
        self.assertIn('```json\n{\n  "scope":', markdown)
        self.assertTrue(markdown.endswith("- `general`\n"))

    def test_renders_empty_sections_explicitly(self) -> None:
        markdown = render_spec_markdown(
            ExecutionSpec(intent="general", objective="Inspect data")
        )

        self.assertIn("## Data Requirements\n\n_None._", markdown)
        self.assertIn("## Preprocessing Steps\n\n_None._", markdown)
        self.assertIn("## Capability Requirements\n\n_None._", markdown)
        self.assertIn("## Engine Hint\n\n_None._", markdown)


if __name__ == "__main__":
    unittest.main()
