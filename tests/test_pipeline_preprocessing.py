from __future__ import annotations

import unittest

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    ExecutionSpec,
    IntentAnalysis,
    PreprocessingStep,
    UserQuery,
)


def governed_step() -> PreprocessingStep:
    return PreprocessingStep(
        name="understand_request",
        order=1,
        step_type="understand",
        required=True,
    )


class StructuredAnalyzer:
    def analyze(
        self,
        query: UserQuery,
        session_context: object,
        user_context: object,
    ) -> IntentAnalysis:
        del query, session_context, user_context
        return IntentAnalysis(
            intent="reason",
            catalog_intent_id="data_query",
            preprocessing_steps=[governed_step()],
        )


class LegacyAnalyzer:
    def analyze(
        self,
        query: UserQuery,
        session_context: object,
        user_context: object,
    ) -> str:
        del query, session_context, user_context
        return "general"


class SpecBuilder:
    def __init__(self) -> None:
        self.received_intent: object = None

    def build(self, query: object, intent: object, *args: object) -> ExecutionSpec:
        self.received_intent = intent
        return ExecutionSpec(
            intent="reason",
            objective="Inspect the corpus",
            preprocessing_steps=[
                PreprocessingStep(
                    name="llm_injected_step",
                    order=99,
                    step_type="analyze",
                )
            ],
        )

    def revise(self, **kwargs: object) -> ExecutionSpec:
        return ExecutionSpec(
            intent="reason",
            objective="Revised objective",
            preprocessing_steps=[
                PreprocessingStep(
                    name="revised_llm_step",
                    order=100,
                    step_type="present",
                )
            ],
        )


def pipeline(analyzer: object, builder: SpecBuilder) -> DataIntelligencePipeline:
    return DataIntelligencePipeline(
        intent_analyzer=analyzer,
        spec_builder=builder,
        spec_confirmation=object(),
        engine_registry=object(),
        evidence_collector=object(),
        synthesizer=object(),
    )


class PipelinePreprocessingTests(unittest.TestCase):
    def test_prepare_attaches_governed_steps_after_llm_build(self) -> None:
        builder = SpecBuilder()

        prepared = pipeline(StructuredAnalyzer(), builder).prepare_spec(
            UserQuery(text="Inspect orders"),
        )

        self.assertEqual(builder.received_intent, "reason")
        self.assertEqual(prepared.intent, "reason")
        self.assertEqual(prepared.intent_analysis.catalog_intent_id, "data_query")
        self.assertEqual(
            [step.name for step in prepared.spec.preprocessing_steps],
            ["understand_request"],
        )

    def test_prepare_normalizes_legacy_string_analyzer(self) -> None:
        builder = SpecBuilder()

        prepared = pipeline(LegacyAnalyzer(), builder).prepare_spec(
            UserQuery(text="Inspect orders"),
        )

        self.assertEqual(builder.received_intent, "general")
        self.assertEqual(prepared.intent_analysis.intent, "general")
        self.assertEqual(prepared.spec.preprocessing_steps, [])

    def test_revision_preserves_governed_steps(self) -> None:
        builder = SpecBuilder()
        subject = pipeline(StructuredAnalyzer(), builder)
        prepared = subject.prepare_spec(
            UserQuery(text="Inspect orders"),
        )

        revised = subject.revise_spec(prepared, prepared.spec, "Focus on totals")

        self.assertEqual(revised.objective, "Revised objective")
        self.assertEqual(
            [step.name for step in revised.preprocessing_steps],
            ["understand_request"],
        )


if __name__ == "__main__":
    unittest.main()
