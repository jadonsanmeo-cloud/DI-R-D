"""HTTP adapter for AXIOM Intent Service."""

from __future__ import annotations

from typing import Any

import httpx

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.intent import IntentAnalysis

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
    ) -> Intent:
        """Return the normalized SDK intent for compatibility with the protocol."""

        return self.analyze_details(
            query,
            corpus_package,
            session_context,
            user_context,
        ).intent

    def analyze_details(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> IntentAnalysis:
        """Return AXIOM catalog classification plus its normalized SDK intent."""

        del corpus_package, session_context, user_context
        payload = {
            "query": query.text,
            "search_type": "hybrid",
            "limit": 1,
        }
        response_payload = self._search_catalog(payload)
        raw_results = response_payload.get("results")
        if not isinstance(raw_results, list) or not raw_results:
            raise RuntimeError("Intent Service catalog search returned no matches.")
        top_match = raw_results[0]
        if not isinstance(top_match, dict):
            raise RuntimeError("Intent Service catalog match must be an object.")
        definition = top_match.get("intent")
        if not isinstance(definition, dict):
            raise RuntimeError("Intent Service catalog match is missing its intent.")
        catalog_intent = str(definition.get("intent_id") or "").strip()
        if not catalog_intent:
            raise RuntimeError("Intent Service catalog match has no intent id.")
        score = top_match.get("score")
        raw_processing_steps = definition.get("processing_steps")
        processing_steps = (
            dict(raw_processing_steps) if isinstance(raw_processing_steps, dict) else {}
        )
        return IntentAnalysis(
            intent=_map_catalog_intent(catalog_intent),
            source="axiom_intent_service",
            catalog_intent=catalog_intent or None,
            score=float(score) if isinstance(score, (int, float)) else None,
            processing_steps=processing_steps,
        )

    def _search_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            raise RuntimeError("Intent Service catalog search request failed.") from exc
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
