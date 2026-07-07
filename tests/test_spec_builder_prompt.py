import json
import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec, UserQuery
from data_intelligence_sdk.spec import SpecBuilderPrompt, SpecContextBuilder


class SpecBuilderPromptTests(unittest.TestCase):
    def test_build_messages_include_query_intent_and_corpus_context(self) -> None:
        prompt = SpecBuilderPrompt()

        messages = prompt.build_messages(
            query=UserQuery("What is total revenue?"),
            intent="reason",
            corpus_package=DataCorpusPackage(
                sources=["sales.csv"],
                schemas={"sales.csv": {"columns": ["country", "revenue"]}},
                metadata={"catalog": {"summary": "Sales dataset"}},
            ),
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("ExecutionSpec", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["query"]["text"], "What is total revenue?")
        self.assertEqual(payload["intent"], "reason")
        self.assertEqual(payload["corpus_package"]["sources"], ["sales.csv"])
        self.assertEqual(
            payload["corpus_package"]["schemas"]["sales.csv"]["columns"],
            ["country", "revenue"],
        )
        self.assertEqual(payload["corpus_summary"]["catalog_summary"], "Sales dataset")
        self.assertEqual(payload["corpus_summary"]["sources"][0]["ref"], "sales.csv")
        self.assertEqual(
            payload["spec_build_context"]["corpus_summary"]["catalog_summary"],
            "Sales dataset",
        )

    def test_build_messages_include_compacted_schema_and_catalog_summary(self) -> None:
        messages = SpecBuilderPrompt().build_messages(
            query=UserQuery("Create a report about orders and document chunks."),
            intent="report",
            corpus_package=DataCorpusPackage(
                sources=[
                    "postgresql://demo/db?schema=vectordb",
                    "postgresql://demo/db",
                ],
                schemas={
                    "tables": {
                        "orders": {
                            "description": "Order revenue records.",
                            "columns": ["order_id", "status", "revenue"],
                            "primary_key": "order_id",
                        }
                    },
                    "vector_collections": {
                        "document_chunks": {
                            "description": "Embedded document chunks.",
                            "columns": ["chunk_id", "content", "embedding"],
                        }
                    },
                },
                metadata={
                    "package": {
                        "schema": "schema.json",
                        "catalog": "catalog.json",
                    },
                    "catalog": {
                        "summary": "Mock corpus package.",
                        "datasets": [
                            {
                                "name": "orders",
                                "kind": "db_table",
                                "description": "Orders table.",
                            },
                            {
                                "name": "document_chunks",
                                "kind": "vectordb_collection",
                                "description": "Document chunks.",
                            },
                        ],
                    },
                },
            ),
        )

        payload = json.loads(messages[1]["content"])
        summary = payload["corpus_summary"]
        self.assertEqual(summary["package_refs"]["schema"], "schema.json")
        self.assertEqual(summary["package_refs"]["catalog"], "catalog.json")
        self.assertEqual(summary["tables"][0]["name"], "orders")
        self.assertEqual(
            summary["vector_collections"][0]["name"], "document_chunks"
        )
        self.assertEqual(summary["datasets"][0]["description"], "Orders table.")
        self.assertEqual(
            payload["spec_build_context"]["task_hints"]["mentioned_tables"],
            ["orders"],
        )

    def test_build_messages_can_receive_prebuilt_spec_context(self) -> None:
        context = SpecContextBuilder().build(
            UserQuery("Create a report about orders."),
            "report",
            DataCorpusPackage(
                sources=["postgresql://demo/db"],
                schemas={"tables": {"orders": {"columns": ["order_id"]}}},
            ),
        )

        messages = SpecBuilderPrompt().build_messages(spec_build_context=context)

        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["query"]["text"], "Create a report about orders.")
        self.assertEqual(payload["spec_build_context"]["intent"], "report")
        self.assertEqual(payload["corpus_summary"]["tables"][0]["name"], "orders")

    def test_revise_messages_include_previous_spec_and_feedback(self) -> None:
        prompt = SpecBuilderPrompt()

        messages = prompt.revise_messages(
            previous_spec=ExecutionSpec(
                intent="reason",
                objective="Calculate total revenue.",
                data_requirements=["sales.csv"],
            ),
            user_feedback="Only completed orders.",
            query=UserQuery("What is total revenue?"),
            intent="reason",
            corpus_package=DataCorpusPackage(sources=["sales.csv"]),
        )

        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["previous_spec"]["objective"], "Calculate total revenue.")
        self.assertEqual(payload["user_feedback"], "Only completed orders.")
        self.assertIn("revise", payload["mode"])

    def test_system_prompt_defines_spec_contract_and_example(self) -> None:
        messages = SpecBuilderPrompt().build_messages(
            query=UserQuery("What is total revenue?"),
            intent="reason",
            corpus_package=DataCorpusPackage(sources=["sales.csv"]),
        )

        system_prompt = messages[0]["content"]
        self.assertIn("ExecutionSpec JSON contract", system_prompt)
        self.assertIn("capability_requirements", system_prompt)
        self.assertIn("Do not include confirmed", system_prompt)
        self.assertIn("Do not invent data sources", system_prompt)
        self.assertIn("Allowed capability names", system_prompt)
        self.assertIn("Example output", system_prompt)
        self.assertIn("hard boundary", system_prompt)
        self.assertIn("data_requirements must equal selected_data_context.selected_sources", system_prompt)
        self.assertIn('"objective"', system_prompt)
        self.assertIn('"data_requirements"', system_prompt)


if __name__ == "__main__":
    unittest.main()
