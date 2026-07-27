from __future__ import annotations

import unittest

from data_intelligence_sdk import ExecutionSpec, IntentAnalysis, PreprocessingStep


class IntentAnalysisContractTests(unittest.TestCase):
    def test_preprocessing_step_preserves_catalog_fields(self) -> None:
        step = PreprocessingStep(
            name="resolve_request_context",
            order=3,
            step_type="resolve_context",
            description="Resolve metrics and dimensions.",
            capability="metric_resolution",
            required=True,
            depends_on=["understand_request"],
        )

        self.assertEqual(step.name, "resolve_request_context")
        self.assertEqual(step.order, 3)
        self.assertEqual(step.step_type, "resolve_context")
        self.assertEqual(step.depends_on, ["understand_request"])

    def test_intent_analysis_contains_normalized_and_catalog_intents(self) -> None:
        analysis = IntentAnalysis(
            intent="report",
            catalog_intent_id="data_query",
            preprocessing_steps=[],
            metadata={"source": "axiom-intent-service"},
        )

        self.assertEqual(analysis.intent, "report")
        self.assertEqual(analysis.catalog_intent_id, "data_query")
        self.assertEqual(analysis.metadata["source"], "axiom-intent-service")

    def test_execution_spec_defaults_to_no_preprocessing_steps(self) -> None:
        spec = ExecutionSpec(intent="general", objective="Inspect the corpus")

        self.assertEqual(spec.preprocessing_steps, [])


if __name__ == "__main__":
    unittest.main()
