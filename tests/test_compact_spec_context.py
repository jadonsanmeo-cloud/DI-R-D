from __future__ import annotations

import json
import unittest
from dataclasses import fields

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from data_intelligence_sdk.spec.context import SpecBuildContext, SpecContextBuilder
from data_intelligence_sdk.spec.prompts.spec_builder import SpecBuilderPrompt


class CompactSpecContextTests(unittest.TestCase):
    def test_spec_context_has_no_task_hints(self) -> None:
        self.assertNotIn("task_hints", {item.name for item in fields(SpecBuildContext)})

    def test_prompt_contains_one_canonical_context_without_duplicates(self) -> None:
        context = SpecContextBuilder().build(
            UserQuery(text="Inspect orders"),
            "reason",
            DataCorpusPackage(
                sources=["orders.csv"],
                schemas={"tables": {"orders": {"columns": ["order_id"]}}},
                metadata={"catalog": {"summary": "Order data"}},
            ),
        )

        messages = SpecBuilderPrompt().build_messages(
            spec_build_context=context,
            selected_data_context={"selected_sources": ["orders.csv"]},
        )
        payload = json.loads(messages[-1]["content"])

        self.assertEqual(
            set(payload),
            {
                "mode",
                "spec_context",
                "selected_data",
                "previous_spec",
                "user_feedback",
            },
        )
        self.assertEqual(payload["spec_context"]["query"]["text"], "Inspect orders")
        self.assertNotIn("task_hints", payload["spec_context"])
        for duplicate in (
            "corpus_package",
            "corpus_summary",
            "session_context",
            "user_context",
            "query",
            "intent",
        ):
            self.assertNotIn(duplicate, payload)


if __name__ == "__main__":
    unittest.main()
