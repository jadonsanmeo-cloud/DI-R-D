import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from examples.basic_workflow import ExampleIntentAnalyzer, create_example_pipeline


class ExampleWorkflowTests(unittest.TestCase):
    def test_default_method_hub_includes_vector_search(self) -> None:
        pipeline = create_example_pipeline(llm=object())

        method_names = {method.name for method in pipeline.method_hub.list_methods()}

        self.assertIn("scan_csv", method_names)
        self.assertIn("search_vector_chunks", method_names)

    def test_intent_analyzer_uses_reason_for_data_questions(self) -> None:
        intent = ExampleIntentAnalyzer().analyze(
            UserQuery("What is the data about?"),
            DataCorpusPackage(sources=["postgresql://demo/db?schema=vectordb"]),
        )

        self.assertEqual(intent, "reason")

    def test_intent_analyzer_uses_report_for_report_requests(self) -> None:
        intent = ExampleIntentAnalyzer().analyze(
            UserQuery("Create a report about this dataset"),
            DataCorpusPackage(sources=["sales.csv"]),
        )

        self.assertEqual(intent, "report")

    def test_intent_analyzer_uses_unknown_for_outliers(self) -> None:
        intent = ExampleIntentAnalyzer().analyze(
            UserQuery("Tell me a joke"),
            DataCorpusPackage(),
        )

        self.assertEqual(intent, "unknown")


if __name__ == "__main__":
    unittest.main()
