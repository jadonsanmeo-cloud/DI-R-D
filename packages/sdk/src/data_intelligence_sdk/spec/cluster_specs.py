"""Cluster-level execution spec preparation and selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.datahub import (
    DataHubCluster,
    DataHubClusteringResult,
)
from data_intelligence_sdk.runtime.llm_client import LLMClient
from data_intelligence_sdk.spec.prompts.cluster_spec_selector import (
    ClusterSpecSelectorPrompt,
)


@dataclass(slots=True)
class ClusterExecutionSpec:
    """Prepared execution spec for one datahub cluster."""

    cluster_id: str
    cluster: DataHubCluster
    execution_spec: ExecutionSpec


class ClusterSpecBuilder(Protocol):
    """Builds one reusable execution spec for each datahub cluster."""

    def build_specs(
        self,
        corpus_package: DataCorpusPackage,
        clustering_result: DataHubClusteringResult,
        intent: Intent,
    ) -> list[ClusterExecutionSpec]:
        """Return prepared specs for all clusters."""


class DefaultClusterSpecBuilder:
    """Builds conservative cluster-level specs without another LLM call."""

    def build_specs(
        self,
        corpus_package: DataCorpusPackage,
        clustering_result: DataHubClusteringResult,
        intent: Intent,
    ) -> list[ClusterExecutionSpec]:
        del corpus_package
        specs = []
        for cluster in clustering_result.clusters:
            constraints = {
                "scope": {
                    "cluster_id": cluster.cluster_id,
                    "cluster_name": cluster.name,
                    "members": [asdict(member) for member in cluster.members],
                    "relationships": list(cluster.relationships),
                },
                "cluster_description": cluster.description,
                "suggested_tasks": list(cluster.suggested_tasks),
                "evidence_required": True,
            }
            execution_spec = ExecutionSpec(
                intent=intent,
                objective=f"Prepare an analysis or report for the {cluster.name} data cluster.",
                data_requirements=[f"cluster:{cluster.cluster_id}"],
                capability_requirements=[
                    CapabilityRequirement(
                        name="inspect_data",
                        description="Inspect the cluster assets, schemas, and descriptions.",
                    ),
                    CapabilityRequirement(
                        name="generate_report",
                        description="Generate a cluster-level answer or report.",
                    ),
                ],
                constraints=constraints,
                engine_hint="report" if intent == "report" else None,
            )
            specs.append(
                ClusterExecutionSpec(
                    cluster_id=cluster.cluster_id,
                    cluster=cluster,
                    execution_spec=execution_spec,
                )
            )
        return specs


class ClusterSpecSelector(Protocol):
    """Selects the best prepared cluster spec for the current user state."""

    def select(
        self,
        *,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        clustering_result: DataHubClusteringResult,
        cluster_specs: list[ClusterExecutionSpec],
        cluster_specs_by_id: dict[str, ClusterExecutionSpec],
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> ClusterExecutionSpec:
        """Return the selected cluster spec."""


class LLMClusterSpecSelector:
    """Uses an LLM to select a prepared cluster spec."""

    def __init__(
        self, llm_client: LLMClient, *, prompt: ClusterSpecSelectorPrompt | None = None
    ) -> None:
        self.llm_client = llm_client
        self.prompt = prompt or ClusterSpecSelectorPrompt()

    def select(
        self,
        *,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        clustering_result: DataHubClusteringResult,
        cluster_specs: list[ClusterExecutionSpec],
        cluster_specs_by_id: dict[str, ClusterExecutionSpec],
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> ClusterExecutionSpec:
        del cluster_specs_by_id
        specs_by_id = {item.cluster_id: item for item in cluster_specs}
        payload = self.llm_client.complete_json(
            self.prompt.select_messages(
                query=query,
                intent=intent,
                corpus_package=corpus_package,
                clustering_result=clustering_result,
                cluster_specs=cluster_specs,
                session_context=session_context,
                user_context=user_context,
                previous_spec=previous_spec,
                user_feedback=user_feedback,
            ),
            stage="cluster-spec-selector",
        )
        cluster_id = payload.get("cluster_id")
        if not isinstance(cluster_id, str) or cluster_id not in specs_by_id:
            raise ValueError("Cluster selector returned an unknown cluster_id.")
        return specs_by_id[cluster_id]
