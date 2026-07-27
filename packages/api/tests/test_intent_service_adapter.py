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
    def test_catalog_failure_uses_configured_local_fallback(self) -> None:
        class FallbackAnalyzer:
            def analyze(
                self,
                query,
                corpus_package,
                session_context=None,
                user_context=None,
            ):
                del query, corpus_package, session_context, user_context
                return "report"

        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"detail": "unavailable"})
            )
        )
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
            fallback_analyzer=FallbackAnalyzer(),
        )

        analysis = analyzer.analyze_details(
            UserQuery(text="Create a report."),
            DataCorpusPackage(sources=["report.html"]),
        )

        self.assertEqual(analysis.intent, "report")
        self.assertEqual(analysis.source, "local_intent_fallback")
        self.assertIsNone(analysis.catalog_intent)

    def test_analyze_details_keeps_processing_steps_from_top_catalog_intent(
        self,
    ) -> None:
        captured: dict[str, object] = {}
        processing_steps = {
            "inspect_schema": {
                "order": 1,
                "step_type": "understand",
                "description": "Inspect the available schema.",
                "capability": "inspect_data",
                "required": True,
                "depends_on": [],
                "follow_up_prompt": None,
            },
            "answer_query": {
                "order": 2,
                "step_type": "analyze",
                "description": "Answer the data question.",
                "capability": "query_structured_data",
                "required": True,
                "depends_on": ["inspect_schema"],
                "follow_up_prompt": None,
            },
        }

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
                                "intent_name": "Data query",
                                "intent_description": "Answer a structured data query.",
                                "processing_steps": processing_steps,
                                "routing_target": "data_intelligence",
                            },
                            "score": 0.92,
                            "matched_by": ["hybrid"],
                        }
                    ],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        analyzer = AxiomIntentServiceAnalyzer(
            base_url="http://intent-service.local",
            client=client,
        )

        analysis = analyzer.analyze_details(
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
        self.assertEqual(analysis.catalog_intent, "data_query")
        self.assertEqual(analysis.processing_steps, processing_steps)
        self.assertEqual(analysis.score, 0.92)
        self.assertEqual(
            captured["url"],
            "http://intent-service.local/api/v1/intent-search",
        )
        self.assertEqual(
            captured["payload"],
            {
                "query": "How many orders do we have?",
                "search_type": "hybrid",
                "limit": 1,
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
                                    "intent_name": "Data description",
                                    "intent_description": "Describe a dataset.",
                                    "processing_steps": {},
                                    "routing_target": "data_intelligence",
                                },
                                "score": 0.87,
                                "matched_by": ["hybrid"],
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

        intent = analyzer.analyze(
            UserQuery(text="Create a report about orders."),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(intent, "report")


if __name__ == "__main__":
    unittest.main()
