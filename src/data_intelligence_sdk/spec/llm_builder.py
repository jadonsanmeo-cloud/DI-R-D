"""LLM-backed execution spec builder."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from data_intelligence_sdk.context import SpecContextBuilder
from data_intelligence_sdk.data_selection import DataSelector
from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.prompts.spec_builder import SpecBuilderPrompt
from data_intelligence_sdk.runtime.llm_client import LLMClient


class LLMSpecBuilder:
    """Builds and revises execution specs through a JSON-capable LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt: object | None = None,
        context_builder: SpecContextBuilder | None = None,
        data_selector: DataSelector | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.prompt = prompt or SpecBuilderPrompt()
        self.context_builder = context_builder or SpecContextBuilder()
        self.data_selector = data_selector

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        spec_build_context = self.context_builder.build(
            query,
            intent,
            corpus_package,
            session_context,
            user_context,
        )
        selected_data_context = self._select_data(spec_build_context)
        payload = self.llm_client.complete_json(
            self.prompt.build_messages(
                spec_build_context=spec_build_context,
                selected_data_context=selected_data_context,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
            )
        )
        return self._payload_to_spec(payload, intent, selected_data_context)

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
        payload = self.llm_client.complete_json(
            self.prompt.revise_messages(
                previous_spec=previous_spec,
                user_feedback=user_feedback,
                spec_build_context=spec_build_context,
                selected_data_context=selected_data_context,
                corpus_package=corpus_package,
                session_context=session_context,
                user_context=user_context,
            )
        )
        return self._payload_to_spec(payload, intent, selected_data_context)

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
        scope = dict(normalized.get("scope", {})) if isinstance(normalized.get("scope"), dict) else {}
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
