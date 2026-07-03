import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.report import ReportEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class ReportEngineTests(unittest.TestCase):
    def test_report_engine_generates_structured_report_from_corpus_metadata(self) -> None:
        corpus = DataCorpusPackage(
            sources=[
                "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
                "postgresql://demo:demo@localhost:5432/data_corpus",
            ],
            schemas={
                "tables": {
                    "orders": {"columns": ["order_id", "revenue"]},
                    "customers": {"columns": ["customer_id", "segment"]},
                },
                "vector_collections": {
                    "document_chunks": {"columns": ["chunk_id", "content"]}
                },
            },
            metadata={
                "catalog": {
                    "summary": "Mock package for report tests.",
                    "datasets": [
                        {
                            "name": "orders",
                            "kind": "db_table",
                            "description": "Order revenue records.",
                        },
                        {
                            "name": "document_chunks",
                            "kind": "vectordb_collection",
                            "description": "Document chunk records.",
                        },
                    ],
                }
            },
        )

        output = ReportEngine().run(
            ExecutionSpec(intent="report", objective="Create a report"),
            corpus,
            EngineRuntimeContext(),
        )

        self.assertEqual(output.engine_name, "report")
        self.assertEqual(output.result["title"], "Data Corpus Report")
        self.assertIn("Mock package", output.result["summary"])
        section_headings = [section["heading"] for section in output.result["sections"]]
        self.assertEqual(section_headings, ["Sources", "Datasets", "Schema"])
        self.assertIn("orders", output.result["sections"][1]["content"])
        self.assertIn("document_chunks", output.result["sections"][2]["content"])
        self.assertEqual(output.trace.steps[0].name, "report_start")


if __name__ == "__main__":
    unittest.main()
