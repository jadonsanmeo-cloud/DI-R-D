import unittest

from data_intelligence_sdk.methods.vector import (
    get_vector_stats_with_connection,
    inspect_vector_chunks_with_connection,
    register_vector_methods,
    search_vector_chunks,
    search_vector_chunks_with_connection,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_obj


class VectorMethodTests(unittest.TestCase):
    def test_register_vector_methods_registers_search_tool(self) -> None:
        method_hub = MethodHub()

        register_vector_methods(method_hub)

        method_names = {method.name for method in method_hub.list_methods()}
        self.assertEqual(
            method_names,
            {"search_vector_chunks", "inspect_vector_chunks", "get_vector_stats"},
        )
        definition = method_hub.get_definition("search_vector_chunks")
        self.assertIn("search_vectordb", definition.capability_names)
        self.assertIn("semantic_search", definition.capability_names)
        self.assertIn("Postgres pgvector", definition.metadata["description"])

    def test_inspect_vector_chunks_returns_bounded_rows(self) -> None:
        rows = [
            (
                "chunk_orders_summary_001",
                "orders_summary",
                "Orders cover revenue by country and status.",
                {"source_file": "raw/txt/orders_summary.txt"},
                1.0,
            )
        ]
        connection = FakeConnection(rows)

        result = inspect_vector_chunks_with_connection(
            "postgresql://demo/db?schema=vectordb",
            limit=4,
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["document_id"], "orders_summary")
        _, params = connection.cursor_obj.executed[0]
        self.assertEqual(params, (4,))

    def test_get_vector_stats_returns_exact_counts(self) -> None:
        connection = FakeConnection([(12, 5)])

        result = get_vector_stats_with_connection(
            "postgresql://demo/db?schema=vectordb",
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(result["chunk_count"], 12)
        self.assertEqual(result["document_count"], 5)
        self.assertEqual(result["rows"][0]["collection"], "vectordb.document_chunks")

    def test_search_vector_chunks_uses_pgvector_when_embedding_is_provided(
        self,
    ) -> None:
        rows = [
            (
                "chunk_orders_summary_001",
                "orders_summary",
                "Orders cover revenue by country and status.",
                {"source_file": "raw/txt/orders_summary.txt"},
                0.97,
            )
        ]
        connection = FakeConnection(rows)

        result = search_vector_chunks_with_connection(
            "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
            query="What is the data about?",
            query_embedding=[0.1, 0.2, 0.3, 0.4, 0.5],
            limit=3,
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(
            result["vectordb"],
            "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
        )
        self.assertEqual(result["query"], "What is the data about?")
        self.assertEqual(result["search_mode"], "vector")
        self.assertEqual(result["matches"][0]["chunk_id"], "chunk_orders_summary_001")
        self.assertEqual(result["matches"][0]["score"], 0.97)
        executed_sql, params = connection.cursor_obj.executed[0]
        self.assertIn("embedding <=>", executed_sql)
        self.assertEqual(params[-1], 3)

    def test_search_vector_chunks_uses_lexical_query_without_embedding(self) -> None:
        rows = [
            (
                "chunk_support_notes_001",
                "support_notes",
                "Support notes describe billing and onboarding tickets.",
                {"source_file": "raw/txt/support_notes.txt"},
                1.0,
            )
        ]
        connection = FakeConnection(rows)

        result = search_vector_chunks_with_connection(
            "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
            query="billing support",
            limit=2,
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(result["search_mode"], "lexical")
        self.assertEqual(result["matches"][0]["document_id"], "support_notes")
        executed_sql, params = connection.cursor_obj.executed[0]
        self.assertIn("content ILIKE", executed_sql)
        self.assertEqual(params, ("%billing support%", 2))

    def test_public_search_tool_does_not_expose_connection_factory(self) -> None:
        annotations = search_vector_chunks.__annotations__

        self.assertIn("vectordb", annotations)
        self.assertIn("query", annotations)
        self.assertNotIn("connection_factory", annotations)


if __name__ == "__main__":
    unittest.main()
