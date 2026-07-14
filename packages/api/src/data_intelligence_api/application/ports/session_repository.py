"""Persistence port for conversational sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class SessionRepository(Protocol):
    async def get_or_create(
        self,
        session_id: str | None,
        *,
        user_id: str | None = None,
    ) -> dict:
        """Return a session record, creating one when no ID is supplied."""

    async def touch(self, session_id: str, at: datetime | None = None) -> None:
        """Update the session activity timestamp."""
