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

        intent = analyzer.analyze(
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

        self.assertEqual(intent, "reason")
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

        intent = analyzer.analyze(
            UserQuery(text="Create a report about orders."),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(intent, "report")


if __name__ == "__main__":
    unittest.main()
