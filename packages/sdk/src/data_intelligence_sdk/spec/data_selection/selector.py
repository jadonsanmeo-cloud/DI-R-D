"""LLM-backed data selection agent."""

from __future__ import annotations

from typing import Any, Protocol

from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.runtime.llm_client import LLMClient
from data_intelligence_sdk.spec.context import SpecBuildContext
from data_intelligence_sdk.spec.data_selection.types import SelectedDataContext
from data_intelligence_sdk.spec.prompts.data_selection import DataSelectionPrompt


class DataSelector(Protocol):
    """Selects data relevant to a task-local spec context."""

    def select(
        self,
        spec_build_context: SpecBuildContext,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> SelectedDataContext:
        """Return selected data context for a task."""


class LLMDataSelector:
    """Uses a JSON-capable LLM to select relevant data from context."""

    def __init__(
        self, llm_client: LLMClient, *, prompt: DataSelectionPrompt | None = None
    ) -> None:
        self.llm_client = llm_client
        self.prompt = prompt or DataSelectionPrompt()

    def select(
        self,
        spec_build_context: SpecBuildContext,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> SelectedDataContext:
        payload = self.llm_client.complete_json(
            self.prompt.select_messages(
                spec_build_context,
                previous_spec=previous_spec,
                user_feedback=user_feedback,
            ),
            stage="data-selector",
        )
        return self._payload_to_selected_context(payload)

    def _payload_to_selected_context(
        self, payload: dict[str, Any]
    ) -> SelectedDataContext:
        return SelectedDataContext(
            selected_sources=_string_list(payload.get("selected_sources", [])),
            selected_tables=_string_list(payload.get("selected_tables", [])),
            selected_columns=_string_list_dict(payload.get("selected_columns", {})),
            selected_vector_collections=_string_list(
                payload.get("selected_vector_collections", [])
            ),
            selected_documents=_string_list(payload.get("selected_documents", [])),
            reasons=_string_list(payload.get("reasons", [])),
            missing_information=_string_list(payload.get("missing_information", [])),
            confidence=_optional_float(payload.get("confidence")),
        )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Expected a list.")
    return [str(item) for item in value]


def _string_list_dict(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return {str(key): _string_list(item) for key, item in value.items()}


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (float, int)):
        raise ValueError("confidence must be a number or null.")
    return float(value)
