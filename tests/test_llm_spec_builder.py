import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec, UserQuery
from data_intelligence_sdk.spec import LLMSpecBuilder


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def complete_json(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


class FakePrompt:
    def __init__(self):
        self.build_called = False
        self.revise_called = False
        self.build_kwargs = None
        self.revise_kwargs = None

    def build_messages(self, **kwargs):
        self.build_called = True
        self.build_kwargs = kwargs
        return [{"role": "user", "content": "custom build prompt"}]

    def revise_messages(self, **kwargs):
        self.revise_called = True
        self.revise_kwargs = kwargs
        return [{"role": "user", "content": "custom revise prompt"}]


class FakeDataSelector:
    def __init__(self):
        self.contexts = []

    def select(self, spec_build_context, previous_spec=None, user_feedback=None):
        del previous_spec, user_feedback
        self.contexts.append(spec_build_context)
        return {
            "selected_tables": ["orders"],
            "selected_sources": ["postgresql://demo/db"],
            "selected_columns": {"orders": ["order_id", "revenue"]},
            "selected_vector_collections": [],
        }


class LLMSpecBuilderTests(unittest.TestCase):
    def test_build_converts_llm_json_to_execution_spec(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Calculate total revenue.",
                    "data_requirements": ["sales.csv"],
                    "capability_requirements": [
                        {
                            "name": "aggregate_data",
                            "description": "Sum revenue values.",
                            "input_schema": {"column": "str"},
                            "output_schema": {"total": "number"},
                            "constraints": {"aggregation": "sum"},
                            "metadata": {"source": "sales.csv"},
                        }
                    ],
                    "constraints": {"metric": "revenue"},
                    "engine_hint": "general_purpose",
                }
            ]
        )
        builder = LLMSpecBuilder(llm)

        spec = builder.build(
            UserQuery("What is the total revenue?"),
            "reason",
            DataCorpusPackage(
                sources=["sales.csv"],
                schemas={"sales.csv": {"columns": ["country", "revenue"]}},
                metadata={"catalog": {"summary": "Sales data"}},
            ),
        )

        self.assertEqual(spec.intent, "reason")
        self.assertEqual(spec.objective, "Calculate total revenue.")
        self.assertFalse(spec.confirmed)
        self.assertEqual(spec.data_requirements, ["sales.csv"])
        self.assertEqual(spec.capability_requirements[0].name, "aggregate_data")
        self.assertEqual(spec.constraints, {"metric": "revenue"})
        self.assertEqual(spec.engine_hint, "general_purpose")
        prompt = llm.messages[0][1]["content"]
        self.assertIn("What is the total revenue?", prompt)
        self.assertIn("sales.csv", prompt)

    def test_revise_sends_previous_spec_and_feedback_to_llm(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Calculate total completed revenue.",
                    "data_requirements": ["sales.csv"],
                    "capability_requirements": [{"name": "aggregate_data"}],
                    "constraints": {"status": "complete"},
                    "engine_hint": None,
                }
            ]
        )
        builder = LLMSpecBuilder(llm)
        previous = ExecutionSpec(
            intent="reason",
            objective="Calculate total revenue.",
            data_requirements=["sales.csv"],
        )

        revised = builder.revise(
            previous_spec=previous,
            user_feedback="Only include completed orders.",
            query=UserQuery("What is the total revenue?"),
            intent="reason",
            corpus_package=DataCorpusPackage(sources=["sales.csv"]),
        )

        self.assertEqual(revised.objective, "Calculate total completed revenue.")
        self.assertEqual(revised.constraints, {"status": "complete"})
        self.assertFalse(revised.confirmed)
        prompt = llm.messages[0][1]["content"]
        self.assertIn("Only include completed orders.", prompt)
        self.assertIn("Calculate total revenue.", prompt)

    def test_builder_uses_injected_prompt_object(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Custom prompt objective.",
                    "data_requirements": [],
                    "capability_requirements": [],
                    "constraints": {},
                    "engine_hint": None,
                }
            ]
        )
        prompt = FakePrompt()
        builder = LLMSpecBuilder(llm, prompt=prompt)

        spec = builder.build(
            UserQuery("ignored"),
            "reason",
            DataCorpusPackage(),
        )

        self.assertTrue(prompt.build_called)
        self.assertIn("spec_build_context", prompt.build_kwargs)
        self.assertEqual(
            prompt.build_kwargs["spec_build_context"].query.text,
            "ignored",
        )
        self.assertEqual(
            llm.messages[0], [{"role": "user", "content": "custom build prompt"}]
        )
        self.assertEqual(spec.objective, "Custom prompt objective.")

    def test_builder_passes_selected_data_context_to_prompt_when_selector_exists(
        self,
    ) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Selected data objective.",
                    "data_requirements": [],
                    "capability_requirements": [],
                    "constraints": {},
                    "engine_hint": None,
                }
            ]
        )
        prompt = FakePrompt()
        selector = FakeDataSelector()
        builder = LLMSpecBuilder(llm, prompt=prompt, data_selector=selector)

        builder.build(
            UserQuery("Create a report about orders."),
            "report",
            DataCorpusPackage(sources=["postgresql://demo/db"]),
        )

        self.assertEqual(len(selector.contexts), 1)
        self.assertEqual(
            prompt.build_kwargs["selected_data_context"],
            {
                "selected_tables": ["orders"],
                "selected_sources": ["postgresql://demo/db"],
                "selected_columns": {"orders": ["order_id", "revenue"]},
                "selected_vector_collections": [],
            },
        )

    def test_builder_normalizes_spec_to_selected_data_context(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Create selected report.",
                    "data_requirements": [
                        "postgresql://demo/db",
                        "postgresql://demo/other",
                    ],
                    "capability_requirements": [{"name": "generate_report"}],
                    "constraints": {
                        "scope": {
                            "tables": ["orders", "customers"],
                            "vector_collections": ["document_chunks", "other_chunks"],
                        },
                        "columns": {
                            "orders": ["order_id", "revenue", "secret"],
                            "customers": ["customer_id"],
                        },
                    },
                    "engine_hint": "report",
                }
            ]
        )

        class SelectedDataSelector:
            def select(self, spec_build_context, previous_spec=None, user_feedback=None):
                del spec_build_context, previous_spec, user_feedback
                return {
                    "selected_sources": ["postgresql://demo/db"],
                    "selected_tables": ["orders"],
                    "selected_columns": {"orders": ["order_id", "revenue"]},
                    "selected_vector_collections": ["document_chunks"],
                    "selected_documents": [],
                    "reasons": ["orders and document chunks were selected"],
                    "missing_information": [],
                    "confidence": 0.9,
                }

        builder = LLMSpecBuilder(llm, data_selector=SelectedDataSelector())

        spec = builder.build(
            UserQuery("Create a report about orders and document chunks."),
            "report",
            DataCorpusPackage(
                sources=["postgresql://demo/db", "postgresql://demo/other"]
            ),
        )

        self.assertEqual(spec.data_requirements, ["postgresql://demo/db"])
        self.assertEqual(spec.constraints["scope"]["tables"], ["orders"])
        self.assertEqual(
            spec.constraints["scope"]["vector_collections"],
            ["document_chunks"],
        )
        self.assertEqual(
            spec.constraints["columns"],
            {"orders": ["order_id", "revenue"]},
        )
        self.assertEqual(
            spec.constraints["selected_data_context"]["selected_tables"],
            ["orders"],
        )

    def test_revise_passes_feedback_to_data_selector_and_uses_revised_selection(
        self,
    ) -> None:
        llm = FakeLLMClient(
            [
                {
                    "objective": "Create orders-only report.",
                    "data_requirements": [
                        "postgresql://demo/db",
                        "postgresql://demo/db?schema=vectordb",
                    ],
                    "capability_requirements": [{"name": "generate_report"}],
                    "constraints": {
                        "scope": {
                            "tables": ["orders"],
                            "vector_collections": ["document_chunks"],
                        },
                        "columns": {
                            "orders": ["order_id", "revenue"],
                            "document_chunks": ["chunk_id", "content"],
                        },
                    },
                    "engine_hint": "report",
                }
            ]
        )

        class FeedbackAwareDataSelector:
            def __init__(self):
                self.feedback = None
                self.previous_spec = None

            def select(
                self,
                spec_build_context,
                previous_spec=None,
                user_feedback=None,
            ):
                del spec_build_context
                self.feedback = user_feedback
                self.previous_spec = previous_spec
                return {
                    "selected_sources": ["postgresql://demo/db"],
                    "selected_tables": ["orders"],
                    "selected_columns": {"orders": ["order_id", "revenue"]},
                    "selected_vector_collections": [],
                    "selected_documents": [],
                    "reasons": ["user feedback removed document chunks"],
                    "missing_information": [],
                    "confidence": 0.8,
                }

        selector = FeedbackAwareDataSelector()
        builder = LLMSpecBuilder(llm, data_selector=selector)

        spec = builder.revise(
            previous_spec=ExecutionSpec(
                intent="report",
                objective="Create a report about orders and document chunks.",
            ),
            user_feedback="khong can document chunks nua",
            query=UserQuery("Create a report about orders and document chunks."),
            intent="report",
            corpus_package=DataCorpusPackage(
                sources=[
                    "postgresql://demo/db",
                    "postgresql://demo/db?schema=vectordb",
                ]
            ),
        )

        self.assertEqual(selector.feedback, "khong can document chunks nua")
        self.assertEqual(
            selector.previous_spec.objective,
            "Create a report about orders and document chunks.",
        )
        self.assertEqual(spec.data_requirements, ["postgresql://demo/db"])
        self.assertEqual(spec.constraints["scope"]["tables"], ["orders"])
        self.assertEqual(spec.constraints["scope"]["vector_collections"], [])
        self.assertNotIn("document_chunks", spec.constraints["columns"])
        self.assertEqual(
            spec.constraints["selected_data_context"]["selected_vector_collections"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
