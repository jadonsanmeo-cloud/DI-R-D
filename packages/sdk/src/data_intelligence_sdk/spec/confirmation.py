"""Human-in-the-loop spec confirmation boundary."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import ExecutionSpec, SessionContext, UserContext
from data_intelligence_sdk.spec.default_confirmation import SpecConfirmationDecision


class SpecConfirmation(Protocol):
    """Confirms or revises a draft spec before engine selection."""

    def confirm(
        self,
        spec: ExecutionSpec,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec | SpecConfirmationDecision:
        """Return a confirmed spec or a revision decision."""
