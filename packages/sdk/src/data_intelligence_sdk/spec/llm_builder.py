"""LLM-backed execution spec builder."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.llm_client import LLMClient
from data_intelligence_sdk.datahub import DataHubClusterer
from data_intelligence_sdk.spec.cluster_specs import (
    ClusterSpecBuilder,
    ClusterSpecSelector,
    DefaultClusterSpecBuilder,
)
from data_intelligence_sdk.spec.context import SpecContextBuilder
from data_intelligence_sdk.spec.data_selection import DataSelector
from data_intelligence_sdk.spec.prompts.spec_builder import SpecBuilderPrompt


class LLMSpecBuilder:
    """Builds and revises execution specs through a JSON-capable LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt: object | None = None,
        context_builder: SpecContextBuilder | None = None,
        data_selector: DataSelector | None = None,
        datahub_clusterer: DataHubClusterer | None = None,
        cluster_spec_builder: ClusterSpecBuilder | None = None,
        cluster_spec_selector: ClusterSpecSelector | None = None,
        max_validation_retries: int = 2,
        require_actionable_spec: bool = False,
        default_missing_requirements: bool = False,
    ) -> None:
        self.llm_client = llm_client
        self.prompt = prompt or SpecBuilderPrompt()
        self.context_builder = context_builder or SpecContextBuilder()
        self.data_selector = data_selector
        self.datahub_clusterer = datahub_clusterer
        self.cluster_spec_builder = cluster_spec_builder or DefaultClusterSpecBuilder()
        self.cluster_spec_selector = cluster_spec_selector
        self.max_validation_retries = max_validation_retries
        self.require_actionable_spec = require_actionable_spec
        self.default_missing_requirements = default_missing_requirements

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        if self._uses_cluster_flow():
            return self._build_from_selected_cluster_spec(
                query=query,
                intent=intent,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
            )

        spec_build_context = self.context_builder.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        selected_data_context = self._select_data(spec_build_context)
        messages = self.prompt.build_messages(
            spec_build_context=spec_build_context,
            selected_data_context=selected_data_context,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
        )
        return self._complete_valid_spec(
            messages,
            intent,
            selected_data_context,
            available_sources=corpus_package.sources,
        )

    def revise(
        self,
        *,
        previous_spec: ExecutionSpec,
        user_feedback: str,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        if self._uses_cluster_flow():
            return self._build_from_selected_cluster_spec(
                query=query,
                intent=intent,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
                previous_spec=previous_spec,
                user_feedback=user_feedback,
            )

        spec_build_context = self.context_builder.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        selected_data_context = self._select_data(
            spec_build_context,
            previous_spec=previous_spec,
            user_feedback=user_feedback,
        )
        messages = self.prompt.revise_messages(
            previous_spec=previous_spec,
            user_feedback=user_feedback,
            spec_build_context=spec_build_context,
            selected_data_context=selected_data_context,
            corpus_package=corpus_package,
            session_context=session_context,
            user_context=user_context,
        )
        return self._complete_valid_spec(
            messages,
            intent,
            selected_data_context,
            available_sources=corpus_package.sources,
        )

    def _complete_valid_spec(
        self,
        messages: list[dict[str, str]],
        intent: Intent,
        selected_data_context: object | None,
        *,
        available_sources: list[str],
    ) -> ExecutionSpec:
        current_messages = list(messages)
        for attempt in range(self.max_validation_retries + 1):
            payload = self.llm_client.complete_json(
                current_messages,
                stage="spec-builder",
            )
            try:
                spec = self._payload_to_spec(payload, intent, selected_data_context)
                if self.default_missing_requirements and available_sources:
                    default_sources = self._default_data_requirements(
                        spec,
                        available_sources,
                    )
                    if not spec.data_requirements or len(default_sources) < len(
                        set(available_sources)
                    ):
                        spec.data_requirements = default_sources
                if (
                    self.default_missing_requirements
                    and not spec.capability_requirements
                ):
                    spec.capability_requirements = [
                        CapabilityRequirement(name=self._default_capability_name(spec))
                    ]
                if self.require_actionable_spec:
                    self._validate_source_boundaries(spec, available_sources)
                return spec
            except ValueError as error:
                if attempt >= self.max_validation_retries:
                    raise
                current_messages = [
                    *messages,
                    {
                        "role": "assistant",
                        "content": json.dumps(payload, ensure_ascii=True),
                    },
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON does not satisfy the ExecutionSpec "
                            f"contract: {error}. Return a corrected JSON object only. "
                            "Keep data_requirements and capability_requirements as arrays, "
                            "constraints as an object, and do not invent data sources."
                            f" Available data sources: {json.dumps(available_sources)}. "
                            "When sources are available, data_requirements must reference at "
                            "least one available source and capability_requirements must not be empty."
                        ),
                    },
                ]
        raise RuntimeError("ExecutionSpec validation retry loop exhausted.")

    def _default_capability_name(self, spec: ExecutionSpec) -> str:
        objective = spec.objective.lower()
        if spec.intent == "report":
            return "generate_report"
        if "summarize" in objective or "summary" in objective:
            return "summarize_corpus"
        return "answer_question"

    def _default_data_requirements(
        self,
        spec: ExecutionSpec,
        available_sources: list[str],
    ) -> list[str]:
        sources = list(dict.fromkeys(available_sources))
        objective = spec.objective.lower()
        if any(
            token in objective
            for token in (
                "vectordb",
                "vector database",
                "vector collection",
                "document chunk",
            )
        ):
            vector_sources = [
                source
                for source in sources
                if "schema=vectordb" in source.lower()
                or source.lower().rstrip("/").endswith("/vectordb")
            ]
            if vector_sources:
                return vector_sources
        return sources

    def _validate_source_boundaries(
        self,
        spec: ExecutionSpec,
        available_sources: list[str],
    ) -> None:
        if not available_sources:
            return
        if not spec.data_requirements:
            raise ValueError(
                "data_requirements must reference at least one available source."
            )
        unknown_sources = [
            source
            for source in spec.data_requirements
            if source not in available_sources
        ]
        if unknown_sources:
            raise ValueError(
                "data_requirements contains sources outside the corpus: "
                + ", ".join(unknown_sources)
            )
        if not spec.capability_requirements:
            raise ValueError(
                "capability_requirements must contain at least one capability."
            )

    def _uses_cluster_flow(self) -> bool:
        return (
            self.datahub_clusterer is not None
            and self.cluster_spec_selector is not None
        )

    def _build_from_selected_cluster_spec(
        self,
        *,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> ExecutionSpec:
        if self.datahub_clusterer is None or self.cluster_spec_selector is None:
            raise RuntimeError("Cluster flow requires a clusterer and selector.")
        clustering_result = self.datahub_clusterer.cluster(corpus_package)
        cluster_specs = self.cluster_spec_builder.build_specs(
            corpus_package,
            clustering_result,
            intent,
        )
        cluster_specs_by_id = {item.cluster_id: item for item in cluster_specs}
        selected = self.cluster_spec_selector.select(
            query=query,
            intent=intent,
            corpus_package=corpus_package,
            clustering_result=clustering_result,
            cluster_specs=cluster_specs,
            cluster_specs_by_id=cluster_specs_by_id,
            session_context=session_context,
            user_context=user_context,
            previous_spec=previous_spec,
            user_feedback=user_feedback,
        )
        return selected.execution_spec

    def _select_data(
        self,
        spec_build_context: object,
        *,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> object | None:
        if self.data_selector is None:
            return None
        return self.data_selector.select(
            spec_build_context,
            previous_spec=previous_spec,
            user_feedback=user_feedback,
        )

    def _payload_to_spec(
        self,
        payload: dict[str, Any],
        intent: Intent,
        selected_data_context: object | None = None,
    ) -> ExecutionSpec:
        capability_payloads = payload.get("capability_requirements", [])
        if not isinstance(capability_payloads, list):
            raise ValueError("capability_requirements must be a list.")

        capabilities = [
            self._payload_to_capability(capability)
            for capability in capability_payloads
        ]
        data_requirements = payload.get("data_requirements", [])
        if not isinstance(data_requirements, list):
            raise ValueError("data_requirements must be a list.")

        constraints = payload.get("constraints", {})
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be a JSON object.")
        constraints = self._normalize_constraints_to_selected_data(
            constraints,
            selected_data_context,
        )

        engine_hint = payload.get("engine_hint")
        if engine_hint is not None and not isinstance(engine_hint, str):
            raise ValueError("engine_hint must be a string or null.")

        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be a non-empty string.")

        return ExecutionSpec(
            intent=intent,
            objective=objective,
            data_requirements=self._normalize_data_requirements(
                data_requirements,
                selected_data_context,
            ),
            capability_requirements=capabilities,
            constraints=constraints,
            confirmed=False,
            engine_hint=engine_hint,
        )

    def _payload_to_capability(self, payload: Any) -> CapabilityRequirement:
        if isinstance(payload, str):
            return CapabilityRequirement(name=payload)
        if not isinstance(payload, dict):
            raise ValueError("Each capability requirement must be an object or string.")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Capability requirement name must be a non-empty string.")
        return CapabilityRequirement(
            name=name,
            description=payload.get("description"),
            input_schema=_dict_or_empty(payload.get("input_schema")),
            output_schema=_dict_or_empty(payload.get("output_schema")),
            constraints=_dict_or_empty(payload.get("constraints")),
            metadata=_dict_or_empty(payload.get("metadata")),
        )

    def _normalize_data_requirements(
        self,
        data_requirements: list[Any],
        selected_data_context: object | None,
    ) -> list[str]:
        selected = _selected_data_to_dict(selected_data_context)
        selected_sources = selected.get("selected_sources")
        if isinstance(selected_sources, list) and selected_sources:
            return [str(source) for source in selected_sources]
        return [str(requirement) for requirement in data_requirements]

    def _normalize_constraints_to_selected_data(
        self,
        constraints: dict[str, Any],
        selected_data_context: object | None,
    ) -> dict[str, Any]:
        selected = _selected_data_to_dict(selected_data_context)
        if not selected:
            return constraints

        normalized = dict(constraints)
        scope = (
            dict(normalized.get("scope", {}))
            if isinstance(normalized.get("scope"), dict)
            else {}
        )
        selected_tables = selected.get("selected_tables")
        selected_vector_collections = selected.get("selected_vector_collections")
        selected_documents = selected.get("selected_documents")
        if isinstance(selected_tables, list):
            scope["tables"] = [str(table) for table in selected_tables]
        if isinstance(selected_vector_collections, list):
            scope["vector_collections"] = [
                str(collection) for collection in selected_vector_collections
            ]
        if isinstance(selected_documents, list):
            scope["documents"] = [str(document) for document in selected_documents]
        if scope:
            normalized["scope"] = scope

        selected_columns = selected.get("selected_columns")
        if isinstance(selected_columns, dict):
            normalized["columns"] = {
                str(name): [str(column) for column in columns]
                for name, columns in selected_columns.items()
                if isinstance(columns, list)
            }

        normalized["selected_data_context"] = selected
        return normalized


def _dict_or_empty(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _selected_data_to_dict(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {}
