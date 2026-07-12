import unittest

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.datahub import (
    DataHubCluster,
    DataHubClusterMember,
    DataHubClusteringResult,
)
from data_intelligence_sdk.spec import (
    DefaultClusterSpecBuilder,
    LLMClusterSpecSelector,
    LLMSpecBuilder,
)


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def complete_json(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


def _cluster_result() -> DataHubClusteringResult:
    return DataHubClusteringResult(
        clusters=[
            DataHubCluster(
                cluster_id="commerce",
                name="Commerce",
                description="Orders and customers.",
                members=[
                    DataHubClusterMember(ref="orders", kind="table", name="orders"),
                    DataHubClusterMember(ref="customers", kind="table", name="customers"),
                ],
                suggested_tasks=["Revenue reporting"],
            ),
            DataHubCluster(
                cluster_id="support",
                name="Support",
                description="Support tickets and notes.",
                members=[
                    DataHubClusterMember(
                        ref="support_tickets",
                        kind="table",
                        name="support_tickets",
                    )
                ],
                suggested_tasks=["Support operations report"],
            ),
        ]
    )


class FakeClusterer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def cluster(self, corpus_package):
        self.calls.append(corpus_package)
        return self.result


class FakeSelector:
    def __init__(self, cluster_id):
        self.cluster_id = cluster_id
        self.calls = []

    def select(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["cluster_specs_by_id"][self.cluster_id]


class ClusterSpecFlowTests(unittest.TestCase):
    def test_default_cluster_spec_builder_prepares_one_spec_per_cluster(self) -> None:
        specs = DefaultClusterSpecBuilder().build_specs(
            DataCorpusPackage(sources=["postgresql://demo/db"]),
            _cluster_result(),
            "report",
        )

        self.assertEqual([spec.cluster_id for spec in specs], ["commerce", "support"])
        self.assertEqual(specs[0].execution_spec.data_requirements, ["cluster:commerce"])
        self.assertEqual(specs[0].execution_spec.constraints["scope"]["cluster_id"], "commerce")
        self.assertEqual(
            specs[0].execution_spec.constraints["scope"]["members"][0]["ref"],
            "orders",
        )

    def test_llm_cluster_spec_selector_uses_empty_query_and_user_context(self) -> None:
        cluster_specs = DefaultClusterSpecBuilder().build_specs(
            DataCorpusPackage(),
            _cluster_result(),
            "report",
        )
        llm = FakeLLMClient(
            [
                {
                    "cluster_id": "support",
                    "reason": "User history prefers support operations.",
                    "confidence": 0.84,
                }
            ]
        )

        selected = LLMClusterSpecSelector(llm).select(
            query=UserQuery(""),
            intent="report",
            corpus_package=DataCorpusPackage(),
            clustering_result=_cluster_result(),
            cluster_specs=cluster_specs,
            cluster_specs_by_id={item.cluster_id: item for item in cluster_specs},
            user_context=UserContext(
                preferences={"focus": "support operations"},
                history=[{"task": "support ticket review"}],
            ),
        )

        self.assertEqual(selected.cluster_id, "support")
        prompt_payload = llm.messages[0][1]["content"]
        self.assertIn('"text": ""', prompt_payload)
        self.assertIn("support operations", prompt_payload)

    def test_llm_spec_builder_cluster_flow_selects_cluster_not_files(self) -> None:
        llm = FakeLLMClient([])
        clusterer = FakeClusterer(_cluster_result())
        selector = FakeSelector("support")
        builder = LLMSpecBuilder(
            llm,
            datahub_clusterer=clusterer,
            cluster_spec_builder=DefaultClusterSpecBuilder(),
            cluster_spec_selector=selector,
        )

        spec = builder.build(
            UserQuery(""),
            "report",
            DataCorpusPackage(sources=["postgresql://demo/db"]),
            user_context=UserContext(preferences={"focus": "support"}),
        )

        self.assertEqual(spec.data_requirements, ["cluster:support"])
        self.assertEqual(spec.constraints["scope"]["cluster_id"], "support")
        self.assertNotIn("selected_data_context", spec.constraints)
        self.assertEqual(selector.calls[0]["query"].text, "")

    def test_revise_cluster_flow_reselects_from_feedback(self) -> None:
        llm = FakeLLMClient([])
        selector = FakeSelector("commerce")
        builder = LLMSpecBuilder(
            llm,
            datahub_clusterer=FakeClusterer(_cluster_result()),
            cluster_spec_builder=DefaultClusterSpecBuilder(),
            cluster_spec_selector=selector,
        )
        previous = ExecutionSpec(
            intent="report",
            objective="Prepare a support operations report.",
            data_requirements=["cluster:support"],
        )

        spec = builder.revise(
            previous_spec=previous,
            user_feedback="switch to commerce revenue",
            query=UserQuery(""),
            intent="report",
            corpus_package=DataCorpusPackage(),
        )

        self.assertEqual(spec.data_requirements, ["cluster:commerce"])
        self.assertEqual(selector.calls[0]["previous_spec"], previous)
        self.assertEqual(selector.calls[0]["user_feedback"], "switch to commerce revenue")


if __name__ == "__main__":
    unittest.main()
