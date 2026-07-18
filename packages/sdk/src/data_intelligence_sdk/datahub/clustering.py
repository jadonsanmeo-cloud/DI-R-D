"""Agents and contracts for clustering a parsed datahub."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from data_intelligence_sdk.core.types import DataCorpusPackage
from data_intelligence_sdk.datahub.prompts import DataHubClusteringPrompt
from data_intelligence_sdk.runtime.llm_client import LLMClient


@dataclass(slots=True)
class DataHubClusterMember:
    """One data asset inside a semantic datahub cluster."""

    ref: str
    kind: str
    name: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class DataHubCluster:
    """A semantic group of related data assets."""

    cluster_id: str
    name: str
    description: str
    members: list[DataHubClusterMember] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    suggested_tasks: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class DataHubClusteringResult:
    """Clusters plus any assets the agent could not confidently group."""

    clusters: list[DataHubCluster] = field(default_factory=list)
    unclustered: list[DataHubClusterMember] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class DataHubClusterer(Protocol):
    """Clusters a parsed datahub/corpus package into related asset groups."""

    def cluster(self, corpus_package: DataCorpusPackage) -> DataHubClusteringResult:
        """Return semantic clusters for the package."""


class LLMDataHubClusterer:
    """Uses a JSON-capable LLM to cluster a parsed datahub."""

    def __init__(
        self, llm_client: LLMClient, *, prompt: DataHubClusteringPrompt | None = None
    ) -> None:
        self.llm_client = llm_client
        self.prompt = prompt or DataHubClusteringPrompt()

    def cluster(self, corpus_package: DataCorpusPackage) -> DataHubClusteringResult:
        payload = self.llm_client.complete_json(
            self.prompt.cluster_messages(corpus_package)
        )
        return self._payload_to_result(payload)

    def _payload_to_result(self, payload: dict[str, Any]) -> DataHubClusteringResult:
        return DataHubClusteringResult(
            clusters=[
                _payload_to_cluster(item) for item in _list(payload.get("clusters", []))
            ],
            unclustered=[
                _payload_to_member(item)
                for item in _list(payload.get("unclustered", []))
            ],
            notes=[str(item) for item in _list(payload.get("notes", []))],
        )


def _payload_to_cluster(payload: Any) -> DataHubCluster:
    if not isinstance(payload, dict):
        raise ValueError("Each cluster must be a JSON object.")
    cluster_id = payload.get("cluster_id")
    name = payload.get("name")
    description = payload.get("description")
    if not isinstance(cluster_id, str) or not cluster_id.strip():
        raise ValueError("cluster_id must be a non-empty string.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("cluster name must be a non-empty string.")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("cluster description must be a non-empty string.")
    return DataHubCluster(
        cluster_id=cluster_id,
        name=name,
        description=description,
        members=[
            _payload_to_member(item) for item in _list(payload.get("members", []))
        ],
        relationships=[str(item) for item in _list(payload.get("relationships", []))],
        suggested_tasks=[
            str(item) for item in _list(payload.get("suggested_tasks", []))
        ],
        confidence=_optional_float(payload.get("confidence")),
    )


def _payload_to_member(payload: Any) -> DataHubClusterMember:
    if not isinstance(payload, dict):
        raise ValueError("Each cluster member must be a JSON object.")
    ref = payload.get("ref")
    kind = payload.get("kind")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("cluster member ref must be a non-empty string.")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("cluster member kind must be a non-empty string.")
    name = payload.get("name")
    reason = payload.get("reason")
    return DataHubClusterMember(
        ref=ref,
        kind=kind,
        name=name if isinstance(name, str) else None,
        reason=reason if isinstance(reason, str) else None,
    )


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Expected a list.")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (float, int)):
        raise ValueError("confidence must be a number or null.")
    return float(value)
