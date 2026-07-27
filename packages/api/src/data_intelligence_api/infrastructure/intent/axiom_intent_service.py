"""HTTP adapter for AXIOM Intent Service."""

from __future__ import annotations

from typing import Any

import httpx

from data_intelligence_sdk.core.types import (
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
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> IntentAnalysis:
        del session_context, user_context
        payload = {
            "limit": 1,
            "query": query.text,
            "search_type": "hybrid",
        }
        response_payload = self._post_intent_search(payload)
        top_hit = _top_search_hit(response_payload)
        resolved_payload = _hit_intent(top_hit)
        catalog_intent_id = str(resolved_payload.get("intent_id") or "").strip()
        catalog_metadata = resolved_payload.get("metadata")
        return IntentAnalysis(
            intent=_map_catalog_intent(catalog_intent_id),
            catalog_intent_id=catalog_intent_id or None,
            preprocessing_steps=_extract_preprocessing_steps(resolved_payload),
            metadata={
                "catalog_metadata": (
                    dict(catalog_metadata) if isinstance(catalog_metadata, dict) else {}
                ),
                "score": top_hit.get("score") if top_hit is not None else None,
                "lexical_score": (
                    top_hit.get("lexical_score") if top_hit is not None else None
                ),
                "semantic_score": (
                    top_hit.get("semantic_score") if top_hit is not None else None
                ),
                "matched_by": top_hit.get("matched_by") if top_hit is not None else [],
            },
        )

    def analyze_details(
        self,
        query: UserQuery,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> IntentAnalysis:
        """Return the full normalized Intent Service classification."""

        return self.analyze(query, session_context, user_context)

    def _post_intent_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            response = client.post(
                f"{self.base_url}/api/v1/intent-search",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Intent Service response must be a JSON object.")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Intent Service search request failed.") from exc
        finally:
            if close_client:
                client.close()


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

def _top_search_hit(response_payload: dict[str, Any]) -> dict[str, Any] | None:
    results = response_payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    top_hit = results[0]
    if not isinstance(top_hit, dict):
        raise ValueError("Intent Service search result must be a JSON object.")
    return top_hit

def _hit_intent(top_hit: dict[str, Any] | None) -> dict[str, Any]:
    if top_hit is None:
        return {}
    intent = top_hit.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("Intent Service search hit must include an intent object.")
    return intent


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
