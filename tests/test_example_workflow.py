import unittest

from examples.basic_workflow import create_example_pipeline


class ExampleWorkflowTests(unittest.TestCase):
    def test_default_method_hub_includes_vector_search(self) -> None:
        pipeline = create_example_pipeline(llm=object())

        method_names = {method.name for method in pipeline.method_hub.list_methods()}

        self.assertIn("scan_csv", method_names)
        self.assertIn("search_vector_chunks", method_names)


if __name__ == "__main__":
    unittest.main()
