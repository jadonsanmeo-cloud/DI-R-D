"""User context persistence boundary."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import UserContext


class UserContextStore(Protocol):
    """Loads and saves user context across tasks."""

    def load(self, user_id: str) -> UserContext:
        """Load context for a user."""

    def save(self, context: UserContext) -> None:
        """Persist context for later tasks."""
