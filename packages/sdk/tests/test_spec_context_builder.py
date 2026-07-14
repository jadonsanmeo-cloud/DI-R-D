import unittest

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.spec import SpecContextBuilder


class SpecContextBuilderTests(unittest.TestCase):
    def test_builds_task_local_context_from_corpus_session_and_user_context(self) -> None:
        builder = SpecContextBuilder(max_recent_turns=2, max_history_items=1)
        corpus = DataCorpusPackage(
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
                "package": {"schema": "schema.json", "catalog": "catalog.json"},
                "catalog": {
                    "summary": "Mock corpus package.",
                    "datasets": [
                        {
                            "name": "orders",
                            "kind": "db_table",
                            "description": "Orders table.",
                        }
                    ],
                },
            },
        )

        context = builder.build(
            UserQuery("Create a report about orders and document chunks revenue."),
            "report",
            corpus,
            SessionContext(
                turns=[
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "middle"},
                    {"role": "user", "content": "Only completed orders."},
                ],
                state={"constraints": {"filters": {"status": "complete"}}},
            ),
            UserContext(
                preferences={"language": "vi", "output_style": "concise"},
                history=[{"task": "previous report"}],
            ),
        )

        self.assertEqual(context.intent, "report")
        self.assertEqual(context.corpus_summary.tables[0]["name"], "orders")
        self.assertEqual(
            context.corpus_summary.vector_collections[0]["name"],
            "document_chunks",
        )
        self.assertEqual(len(context.session_brief.recent_turns), 2)
        self.assertEqual(
            context.session_brief.relevant_constraints,
            {"filters": {"status": "complete"}},
        )
        self.assertEqual(context.user_brief.preferences["language"], "vi")
        self.assertEqual(context.task_hints.mentioned_tables, ["orders"])
        self.assertEqual(
            context.task_hints.mentioned_vector_collections,
            ["document_chunks"],
        )
        self.assertEqual(context.task_hints.metrics, ["revenue"])
        self.assertEqual(context.task_hints.output_type, "report")


if __name__ == "__main__":
    unittest.main()
