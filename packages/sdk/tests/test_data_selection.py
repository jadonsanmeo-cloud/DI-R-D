import json
import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec, UserQuery
from data_intelligence_sdk.spec import (
    DataSelectionPrompt,
    LLMDataSelector,
    SelectedDataContext,
    SpecContextBuilder,
)


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def complete_json(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


class DataSelectionTests(unittest.TestCase):
    def test_data_selection_prompt_includes_context_contract_and_example(self) -> None:
        context = SpecContextBuilder().build(
            UserQuery("Create a report about orders and document chunks."),
            "report",
            DataCorpusPackage(
                sources=["postgresql://demo/db?schema=vectordb", "postgresql://demo/db"],
                schemas={
                    "tables": {"orders": {"columns": ["order_id", "revenue"]}},
                    "vector_collections": {
                        "document_chunks": {"columns": ["chunk_id", "content"]}
                    },
                },
            ),
        )

        messages = DataSelectionPrompt().select_messages(context)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("SelectedDataContext JSON contract", messages[0]["content"])
        self.assertIn("Example output", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["spec_build_context"]["intent"], "report")
        self.assertEqual(
            payload["spec_build_context"]["task_hints"]["mentioned_tables"],
            ["orders"],
        )

    def test_llm_data_selector_converts_llm_json_to_selected_data_context(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "selected_sources": [
                        "postgresql://demo/db",
                        "postgresql://demo/db?schema=vectordb",
                    ],
                    "selected_tables": ["orders"],
                    "selected_columns": {"orders": ["order_id", "revenue"]},
                    "selected_vector_collections": ["document_chunks"],
                    "selected_documents": [],
                    "reasons": [
                        "The query mentions orders.",
                        "The query mentions document chunks.",
                    ],
                    "missing_information": [],
                    "confidence": 0.93,
                }
            ]
        )
        context = SpecContextBuilder().build(
            UserQuery("Create a report about orders and document chunks."),
            "report",
            DataCorpusPackage(
                sources=["postgresql://demo/db?schema=vectordb", "postgresql://demo/db"],
                schemas={
                    "tables": {"orders": {"columns": ["order_id", "revenue"]}},
                    "vector_collections": {
                        "document_chunks": {"columns": ["chunk_id", "content"]}
                    },
                },
            ),
        )

        selected = LLMDataSelector(llm).select(context)

        self.assertIsInstance(selected, SelectedDataContext)
        self.assertEqual(selected.selected_tables, ["orders"])
        self.assertEqual(selected.selected_columns["orders"], ["order_id", "revenue"])
        self.assertEqual(selected.selected_vector_collections, ["document_chunks"])
        self.assertEqual(selected.confidence, 0.93)
        self.assertIn("document_chunks", llm.messages[0][1]["content"])

    def test_data_selection_prompt_includes_previous_spec_and_revision_feedback(
        self,
    ) -> None:
        context = SpecContextBuilder().build(
            UserQuery("Create a report about orders and document chunks."),
            "report",
            DataCorpusPackage(sources=["postgresql://demo/db"]),
        )

        messages = DataSelectionPrompt().select_messages(
            context,
            previous_spec=ExecutionSpec(
                intent="report",
                objective="Create a report about orders and document chunks.",
            ),
            user_feedback="khong can document chunks nua",
        )

        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["user_feedback"], "khong can document chunks nua")
        self.assertEqual(
            payload["previous_spec"]["objective"],
            "Create a report about orders and document chunks.",
        )
        self.assertIn("latest instruction", messages[0]["content"])
        self.assertIn("Revision example", messages[0]["content"])
        self.assertIn("select only A", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
