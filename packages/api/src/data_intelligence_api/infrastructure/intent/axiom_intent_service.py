"""HTTP adapter for AXIOM Intent Service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    IntentAnalysis,
    PreprocessingStep,
    SessionContext,
    UserContext,
    UserQuery,
)

_PREPROCESSING_STEP_TYPES = {
    "understand",
    "clarify",
    "resolve_context",
    "retrieve_data",
    "validate_data",
}

_ROLE_MAP = {
    "human": "user",
    "user": "user",
    "view": "assistant",
    "ai": "assistant",
    "assistant": "assistant",
    "system": "system",
}

_REPORT_INTENTS = {
    "comparative_analysis",
    "data_description",
    "data_visualization",
}

_REASONING_INTENTS = {
    "alerting_monitoring",
    "anomaly_detection",
    "data_quality_check",
    "data_query",
    "forecasting",
    "optimization",
    "recommendation",
    "root_cause_analysis",
}


class AxiomIntentServiceAnalyzer:
    """Use AXIOM Intent Service as the pipeline intent analyzer."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> IntentAnalysis:
        del corpus_package, user_context
        payload = {
            "query": query.text,
            "history": _history_from_session_context(session_context),
        }
        response_payload = self._post_prediction(payload)
        resolved_intent = response_payload.get("resolved_intent")
        resolved_payload = resolved_intent if isinstance(resolved_intent, dict) else {}
        catalog_intent_id = str(
            resolved_payload.get("intent_id")
            or response_payload.get("primary_intent")
            or ""
        ).strip()
        catalog_metadata = resolved_payload.get("metadata")
        return IntentAnalysis(
            intent=_map_catalog_intent(response_payload.get("primary_intent")),
            catalog_intent_id=catalog_intent_id or None,
            preprocessing_steps=_extract_preprocessing_steps(resolved_payload),
            metadata={
                "catalog_metadata": (
                    dict(catalog_metadata) if isinstance(catalog_metadata, dict) else {}
                ),
                "confidence": response_payload.get("confidence"),
                "language": response_payload.get("language"),
            },
        )

    def analyze_details(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> IntentAnalysis:
        """Return the full normalized Intent Service classification."""

        return self.analyze(query, corpus_package, session_context, user_context)

    def _post_prediction(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            response = client.post(
                f"{self.base_url}/api/v1/intent-predictions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Intent Service response must be a JSON object.")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Intent Service prediction request failed.") from exc
        finally:
            if close_client:
                client.close()


def _history_from_session_context(
    session_context: SessionContext | None,
) -> list[dict[str, str]]:
    if session_context is None:
        return []
    return list(_normalize_turns(session_context.turns))


def _normalize_turns(turns: Iterable[dict[str, Any]]) -> Iterable[dict[str, str]]:
    for turn in turns:
        role = _ROLE_MAP.get(str(turn.get("role", "")).lower())
        text = turn.get("text", turn.get("content"))
        if role is None or text is None:
            continue
        text_value = str(text).strip()
        if not text_value:
            continue
        yield {"role": role, "text": text_value}


def _map_catalog_intent(value: object) -> Intent:
    intent_id = str(value or "").strip()
    if intent_id in {"reason", "report", "general", "unknown"}:
        return intent_id  # type: ignore[return-value]
    if intent_id in _REPORT_INTENTS:
        return "report"
    if intent_id in _REASONING_INTENTS:
        return "reason"
    if intent_id == "unknown_intent":
        return "unknown"
    return "general"


def _extract_preprocessing_steps(
    resolved_intent: dict[str, Any],
) -> list[PreprocessingStep]:
    processing_steps = resolved_intent.get("processing_steps")
    if not isinstance(processing_steps, dict):
        return []

    steps: list[PreprocessingStep] = []
    for name, raw_step in processing_steps.items():
        if not isinstance(raw_step, dict):
            continue
        step_type = str(raw_step.get("step_type") or "").strip()
        if step_type not in _PREPROCESSING_STEP_TYPES:
            continue
        raw_dependencies = raw_step.get("depends_on")
        dependencies = (
            [str(value) for value in raw_dependencies]
            if isinstance(raw_dependencies, list)
            else []
        )
        steps.append(
            PreprocessingStep(
                name=str(name),
                order=int(raw_step.get("order", 0)),
                step_type=step_type,
                description=_optional_string(raw_step.get("description")),
                capability=_optional_string(raw_step.get("capability")),
                required=bool(raw_step.get("required", False)),
                depends_on=dependencies,
            )
        )
    return sorted(steps, key=lambda step: (step.order, step.name))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
