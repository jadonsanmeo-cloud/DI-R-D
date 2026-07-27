import json
import unittest

import httpx

from data_intelligence_sdk.core.types import (
    UserQuery,
)

from data_intelligence_api.infrastructure.intent.axiom_intent_service import (
    AxiomIntentServiceAnalyzer,
)


class AxiomIntentServiceAnalyzerTests(unittest.TestCase):
    def test_analyze_searches_intent_catalog(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "intent": {
                                "intent_id": "data_query",
                                "intent_name": "Data Query",
                                "intent_description": "Retrieve data.",
                                "processing_steps": {},
                                "metadata": {"domain": "analytics"},
                            },
                            "score": 0.92,
                            "lexical_score": 0.81,
                            "semantic_score": 0.94,
                            "matched_by": ["semantic", "lexical"],
                        }
                    ],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
        )

        analysis = analyzer.analyze(
            UserQuery(text="How many orders do we have?"),
        )

        self.assertEqual(analysis.intent, "reason")
        self.assertEqual(analysis.catalog_intent_id, "data_query")
        self.assertEqual(analysis.metadata["score"], 0.92)
        self.assertEqual(
            captured["url"],
            "http://intent-service.local/api/v1/intent-search",
        )
        self.assertEqual(
            captured["payload"],
            {
                "limit": 1,
                "query": "How many orders do we have?",
                "search_type": "hybrid",
            },
        )

    def test_analyze_maps_report_like_catalog_intents_to_report(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "results": [
                            {
                                "intent": {
                                    "intent_id": "data_description",
                                    "intent_name": "Data Description",
                                    "intent_description": "Summarize data.",
                                    "processing_steps": {},
                                },
                                "score": 0.87,
                                "matched_by": ["semantic"],
                            }
                        ],
                    },
                )
            )
        )
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local/",
            client=client,
        )

        analysis = analyzer.analyze(UserQuery(text="Create a report about orders."))

        self.assertEqual(analysis.intent, "report")

    def test_analyze_filters_and_orders_governed_preprocessing_steps(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "results": [
                            {
                                "intent": {
                                    "intent_id": "data_query",
                                    "intent_name": "Data Query",
                                    "intent_description": "Retrieve data.",
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
                                "score": 0.91,
                                "matched_by": ["semantic", "lexical"],
                            }
                        ],
                    },
                )
            )
        )
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
        )

        analysis = analyzer.analyze(UserQuery(text="Inspect orders."))

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
