import json
import unittest

import httpx

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    SessionContext,
    UserQuery,
)

from data_intelligence_api.infrastructure.intent.axiom_intent_service import (
    AxiomIntentServiceAnalyzer,
)


class AxiomIntentServiceAnalyzerTests(unittest.TestCase):
    def test_analyze_posts_query_and_session_history_without_datahub(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "primary_intent": "data_query",
                    "secondary_intents": [],
                    "confidence": 0.92,
                    "language": "en",
                    "reasoning": "The user asks for data retrieval.",
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
        )

        analysis = analyzer.analyze(
            UserQuery(text="How many orders do we have?"),
            DataCorpusPackage(
                sources=["orders.csv"],
                schemas={"orders": {"columns": ["order_id"]}},
                metadata={"description": "must not be sent to intent service"},
            ),
            SessionContext(
                turns=[
                    {"role": "human", "content": "I care about orders."},
                    {"role": "view", "text": "Okay."},
                    {"role": "system", "content": "Use concise answers."},
                ]
            ),
        )

        self.assertEqual(analysis.intent, "reason")
        self.assertEqual(analysis.catalog_intent_id, "data_query")
        self.assertEqual(
            captured["url"],
            "http://intent-service.local/api/v1/intent-predictions",
        )
        self.assertEqual(
            captured["payload"],
            {
                "query": "How many orders do we have?",
                "history": [
                    {"role": "user", "text": "I care about orders."},
                    {"role": "assistant", "text": "Okay."},
                    {"role": "system", "text": "Use concise answers."},
                ],
            },
        )

    def test_analyze_maps_report_like_catalog_intents_to_report(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "primary_intent": "data_description",
                        "secondary_intents": [],
                        "confidence": 0.87,
                        "language": "en",
                        "reasoning": "The user asks for a narrative report.",
                    },
                )
            )
        )
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local/",
            client=client,
        )

        analysis = analyzer.analyze(
            UserQuery(text="Create a report about orders."),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(analysis.intent, "report")

    def test_analyze_filters_and_orders_governed_preprocessing_steps(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "primary_intent": "data_query",
                        "confidence": 0.91,
                        "language": "en",
                        "resolved_intent": {
                            "intent_id": "data_query",
                            "metadata": {"domain": "analytics"},
                            "processing_steps": {
                                "present_answer": {
                                    "order": 8,
                                    "step_type": "present",
                                    "required": True,
                                },
                                "retrieve_sources": {
                                    "order": 4,
                                    "step_type": "retrieve_data",
                                    "description": "Retrieve matching sources.",
                                    "capability": "corpus_retrieval",
                                    "required": True,
                                    "depends_on": ["resolve_request_context"],
                                },
                                "understand_request": {
                                    "order": 1,
                                    "step_type": "understand",
                                    "required": True,
                                    "depends_on": [],
                                },
                                "analyze_data": {
                                    "order": 6,
                                    "step_type": "analyze",
                                    "required": True,
                                },
                                "resolve_request_context": {
                                    "order": 3,
                                    "step_type": "resolve_context",
                                    "required": True,
                                    "depends_on": ["understand_request"],
                                },
                            },
                        },
                    },
                )
            )
        )
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
        )

        analysis = analyzer.analyze(
            UserQuery(text="Inspect orders."),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(analysis.catalog_intent_id, "data_query")
        self.assertEqual(
            [step.name for step in analysis.preprocessing_steps],
            ["understand_request", "resolve_request_context", "retrieve_sources"],
        )
        self.assertEqual(
            [step.step_type for step in analysis.preprocessing_steps],
            ["understand", "resolve_context", "retrieve_data"],
        )
        self.assertEqual(
            analysis.preprocessing_steps[-1].depends_on,
            ["resolve_request_context"],
        )
        self.assertEqual(analysis.metadata["catalog_metadata"], {"domain": "analytics"})


if __name__ == "__main__":
    unittest.main()
