"""Execution spec drafting contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    DataHubContext,
    ExecutionSpec,
    Intent,
    SessionContext,
    UserContext,
    UserQuery,
)


class SpecBuilder(Protocol):
    """Builds a draft execution spec from intent and data context."""

    def build(
        self,
        query: UserQuery,
        intent: Intent,
        datahub: DataHubContext,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec:
        """Return a draft execution spec."""
