from __future__ import annotations

import unittest

from data_intelligence_sdk.core.types import IntentAnalysis, PreprocessingStep, UserQuery
from data_intelligence_sdk.spec.markdown_builder import LLMMarkdownSpecBuilder


class SequencedTextClient:
    def __init__(self) -> None:
        self.responses = ["invalid", _valid_markdown()]
        self.text_calls = 0
        self.stages: list[str] = []

    def complete_text(self, messages: list[dict[str, str]], *, stage: str) -> str:
        del messages
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
