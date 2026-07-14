"""Intent analysis contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)


class IntentAnalyzer(Protocol):
    """Infers task intent from the user query and available data context."""

    def analyze(
        self,
        query: UserQuery,
        corpus_package: DataCorpusPackage,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> Intent:
        """Return one intent value from the supported intent list."""
