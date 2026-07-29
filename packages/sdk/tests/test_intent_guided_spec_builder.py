import json
import unittest

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    ExecutionSpec,
    UserQuery,
)
from data_intelligence_sdk.intent import IntentAnalysis
from data_intelligence_sdk.spec.llm_builder import LLMSpecBuilder


PROCESSING_STEPS = {
    "inspect_schema": {
        "order": 1,
        "step_type": "understand",
        "description": "Inspect the available schema.",
        "capability": "inspect_data",
        "required": True,
        "depends_on": [],
        "follow_up_prompt": None,
    },
    "answer_query": {
        "order": 2,
        "step_type": "analyze",
        "description": "Answer the structured data question.",
        "capability": "query_structured_data",
        "required": True,
        "depends_on": ["inspect_schema"],
        "follow_up_prompt": None,
    },
}


class _IntentAnalyzer:
    def __init__(self, analysis: IntentAnalysis) -> None:
        self.analysis = analysis

    def analyze_details(self, query, session_context, user_context):
        del query, session_context, user_context
        return self.analysis


class _IntentAwareSpecBuilder:
    def __init__(self) -> None:
        self.analysis = None

    def build(self, query, intent, session_context, user_context):
        raise AssertionError("pipeline dropped detailed intent analysis")

    def build_with_intent_analysis(
        self,
        query,
        intent_analysis,
        session_context,
        user_context,
    ):
        del session_context, user_context
        self.analysis = intent_analysis
        return ExecutionSpec(intent=intent_analysis.intent, objective=query.text)


class _CapturingLLMClient:
    def __init__(self) -> None:
        self.messages = None

    def complete_json(self, messages, *, stage):
        self.messages = messages
        self.stage = stage
        return {
            "objective": "Answer the structured data question.",
            "data_requirements": ["orders.csv"],
            "capability_requirements": [
                {"name": "inspect_data"},
                {"name": "query_structured_data"},
            ],
            "constraints": {},
            "engine_hint": None,
        }


class IntentGuidedSpecBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = IntentAnalysis(
            intent="reason",
            source="axiom_intent_service",
            catalog_intent="data_query",
            processing_steps=PROCESSING_STEPS,
            score=0.92,
        )

    def test_pipeline_forwards_detailed_intent_analysis_to_spec_builder(self) -> None:
        spec_builder = _IntentAwareSpecBuilder()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=_IntentAnalyzer(self.analysis),
            spec_builder=spec_builder,
            spec_confirmation=object(),
            engine_registry=object(),
        )

        prepared = pipeline.prepare_spec(
            UserQuery(text="How many orders do we have?"),
        )

        self.assertIs(spec_builder.analysis, self.analysis)
        self.assertIs(prepared.intent_analysis, self.analysis)

    def test_llm_spec_builder_adds_processing_steps_to_prompt_context(self) -> None:
        llm_client = _CapturingLLMClient()
        builder = LLMSpecBuilder(llm_client)

        builder.build_with_intent_analysis(
            UserQuery(text="How many orders do we have?"),
            self.analysis,
        )

        self.assertEqual(llm_client.stage, "spec-builder")
        task = json.loads(llm_client.messages[1]["content"])
        self.assertEqual(
            task["spec_context"]["intent_analysis"]["processing_steps"],
            PROCESSING_STEPS,
        )


if __name__ == "__main__":
    unittest.main()
