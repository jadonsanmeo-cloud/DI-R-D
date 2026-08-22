from __future__ import annotations

import json
import unittest

from data_intelligence_sdk.core.types import (
    IntentAnalysis,
    PreprocessingStep,
    UserQuery,
)
from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.memory import MemoryCard, MemoryContext, MemoryScope
from data_intelligence_sdk.runtime.logger import InMemoryRuntimeLogger
from data_intelligence_sdk.spec.markdown_builder import LLMMarkdownSpecBuilder


class SequencedTextClient:
    def __init__(self) -> None:
        self.responses = ["invalid", _valid_markdown()]
        self.text_calls = 0
        self.stages: list[str] = []
        self.messages: list[list[dict[str, str]]] = []

    def complete_text(self, messages: list[dict[str, str]], *, stage: str) -> str:
        self.messages.append(messages)
        self.text_calls += 1
        self.stages.append(stage)
        return self.responses.pop(0)

    def complete_json(self, messages: list[dict[str, str]]) -> dict:
        raise AssertionError("Markdown builder must not request JSON completion")


def _valid_markdown() -> str:
    return """# Interactive Execution Spec

## User Request

Create a report.

## Intent

Report generation.

## Preparation Guidance

Retrieve relevant context.

## Execution Instructions

Retrieve all required documents and cite every source used.

## Expected Output

A Markdown report.
"""


class InteractiveMarkdownBuilderTests(unittest.TestCase):
    def test_pipeline_passes_same_request_memory_context_to_markdown_builder(
        self,
    ) -> None:
        scope = MemoryScope(tenant_id="test-org", user_id="user-1")
        memory_context = MemoryContext(
            cards=(
                MemoryCard(
                    "semantic-1", "semantic", "Private guidance.", 0.9, 0.8, scope
                ),
            ),
            loaded=True,
            mode="active",
        )

        class Analyzer:
            def analyze(self, query, session_context, user_context):
                del query, session_context, user_context
                return IntentAnalysis(intent="report")

        class CapturingBuilder:
            received_memory_context = None

            def build(self, query, analysis, memory_context=None):
                del query, analysis
                self.received_memory_context = memory_context
                return _valid_markdown()

        builder = CapturingBuilder()
        logger = InMemoryRuntimeLogger()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=Analyzer(),
            spec_builder=object(),
            spec_confirmation=object(),
            engine_registry=object(),
            markdown_spec_builder=builder,
            logger=logger,
        )

        pipeline.prepare_markdown(
            UserQuery(text="Compare the reports"),
            memory_context=memory_context,
        )

        self.assertIs(builder.received_memory_context, memory_context)
        selected = [
            payload
            for event, payload in logger.events
            if event == "memory.context.selected"
        ]
        self.assertEqual(selected[0]["memory_ids"], ["semantic-1"])
        self.assertNotIn("Private guidance.", str(selected[0]))
        self.assertIn(
            "prompt.envelope.composed",
            [event for event, _ in logger.events],
        )

    def test_build_prompt_uses_axiom_identity_and_deep_memory_view(self) -> None:
        client = SequencedTextClient()
        client.responses = [_valid_markdown()]
        builder = LLMMarkdownSpecBuilder(client)
        analysis = IntentAnalysis(intent="report", catalog_intent_id="data_query")
        scope = MemoryScope(tenant_id="test-org", user_id="user-1")
        memory_context = MemoryContext(
            cards=(
                MemoryCard(
                    "profile-1", "profile", "The user is an analyst.", 0.9, 0.8, scope
                ),
                MemoryCard(
                    "semantic-1",
                    "semantic",
                    "Reporting year differs from publication year.",
                    0.9,
                    0.8,
                    scope,
                ),
                MemoryCard(
                    "procedure-1",
                    "procedure",
                    "Normalize reporting periods before comparison.",
                    0.9,
                    0.8,
                    scope,
                ),
            )
        )

        builder.build(
            UserQuery(text="Compare the reports"),
            analysis,
            memory_context=memory_context,
        )

        system = client.messages[0][0]["content"]
        self.assertIn("You are AXIOM, a data intelligence assistant", system)
        self.assertIn(
            "Relevant Knowledge:\n- Reporting year differs from publication year.",
            system,
        )
        self.assertIn(
            "Procedures:\n- Normalize reporting periods before comparison.",
            system,
        )
        self.assertNotIn("Profile:", system)
        self.assertIn("<axiom_memory>", system)
        self.assertEqual(
            json.loads(client.messages[0][1]["content"])["query"],
            "Compare the reports",
        )

    def test_builds_and_retries_direct_markdown(self) -> None:
        client = SequencedTextClient()
        builder = LLMMarkdownSpecBuilder(client, max_validation_retries=1)
        analysis = IntentAnalysis(
            intent="report",
            catalog_intent_id="data_query",
            preprocessing_steps=[
                PreprocessingStep(
                    name="retrieve_sources",
                    order=4,
                    step_type="retrieve_data",
                    description="Retrieve relevant corpus data.",
                )
            ],
        )

        markdown = builder.build(UserQuery(text="Create a report"), analysis)

        self.assertEqual(markdown, _valid_markdown())
        self.assertEqual(client.text_calls, 2)
        self.assertEqual(
            client.stages,
            ["markdown-spec-builder", "markdown-spec-builder"],
        )
        self.assertNotIn("capability_requirements", markdown)
        self.assertNotIn("data_requirements", markdown)


if __name__ == "__main__":
    unittest.main()
