import json
import unittest

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.datahub import (
    DataHubCluster,
    DataHubClusterMember,
    LLMDataHubClusterer,
)
from data_intelligence_sdk.datahub.prompts import DataHubClusteringPrompt


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def complete_json(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


class DataHubClusteringTests(unittest.TestCase):
    def test_prompt_includes_schema_descriptions_and_catalog(self) -> None:
        corpus = DataCorpusPackage(
            sources=["postgresql://demo/db"],
            schemas={
                "tables": {
                    "orders": {
                        "description": "Sales order records.",
                        "columns": ["order_id", "customer_id", "revenue"],
                    }
                }
            },
            metadata={
                "catalog": {
                    "summary": "Commerce package.",
                    "datasets": [
                        {
                            "name": "orders",
                            "kind": "db_table",
                            "description": "Orders dataset.",
                        }
                    ],
                }
            },
        )

        messages = DataHubClusteringPrompt().cluster_messages(corpus)

        self.assertIn("DataHubClusteringResult JSON contract", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["corpus_package"]["schemas"]["tables"]["orders"]["description"], "Sales order records.")
        self.assertEqual(payload["corpus_package"]["metadata"]["catalog"]["summary"], "Commerce package.")

    def test_llm_clusterer_converts_json_to_cluster_result(self) -> None:
        llm = FakeLLMClient(
            [
                {
                    "clusters": [
                        {
                            "cluster_id": "customer_commerce",
                            "name": "Customer Commerce",
                            "description": "Customer and order data.",
                            "members": [
                                {
                                    "ref": "orders",
                                    "kind": "table",
                                    "name": "orders",
                                    "reason": "Orders reference customers.",
                                }
                            ],
                            "relationships": ["orders.customer_id relates to customers.customer_id"],
                            "suggested_tasks": ["Create a revenue report."],
                            "confidence": 0.91,
                        }
                    ],
                    "unclustered": [],
                    "notes": ["One obvious business cluster."],
                }
            ]
        )

        result = LLMDataHubClusterer(llm).cluster(DataCorpusPackage(sources=["x"]))

        self.assertEqual(result.clusters[0].cluster_id, "customer_commerce")
        self.assertEqual(result.clusters[0].members[0].ref, "orders")
        self.assertEqual(result.clusters[0].confidence, 0.91)
        self.assertEqual(result.notes, ["One obvious business cluster."])

    def test_cluster_dataclasses_are_exported(self) -> None:
        cluster = DataHubCluster(
            cluster_id="support",
            name="Support",
            description="Support data.",
            members=[
                DataHubClusterMember(
                    ref="support_tickets",
                    kind="table",
                    name="support_tickets",
                    reason="Ticket records.",
                )
            ],
        )

        self.assertEqual(cluster.members[0].kind, "table")


if __name__ == "__main__":
    unittest.main()
