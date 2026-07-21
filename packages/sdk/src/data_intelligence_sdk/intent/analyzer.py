"""Intent analysis contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)


@dataclass(frozen=True, slots=True)
class IntentAnalysis:
    """Normalized pipeline intent plus classification metadata from its source."""

    intent: Intent
    source: str
    catalog_intent: str | None = None
    confidence: float | None = None
    score: float | None = None

    def event_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "intent": self.intent,
            "source": self.source,
        }
        if self.catalog_intent is not None:
            payload["catalog_intent"] = self.catalog_intent
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.score is not None:
            payload["score"] = self.score
        return payload


class IntentAnalyzer(Protocol):
    """Infers task intent from the user query and available data context."""

    def analyze(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> Intent:
        """Return one normalized intent value from the supported intent list."""
