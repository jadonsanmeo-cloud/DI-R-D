"""Domain types and errors for resumable response runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

RunStatus = Literal[
    "preparing",
    "awaiting_confirmation",
    "revising",
    "executing",
    "completed",
    "failed",
    "expired",
]


class RunStoreError(RuntimeError):
    """Base error for persisted response state."""


class RunNotFoundError(RunStoreError):
    """Raised for unknown response IDs and invalid confirmation tokens."""


class RunConflictError(RunStoreError):
    """Raised for stale revisions or already-claimed runs."""


class RunExpiredError(RunStoreError):
    """Raised when a pending response has expired."""


@dataclass(slots=True)
class StoredRun:
    response_id: str
    status: RunStatus
    current_revision: int
    token_hash: str
    request_payload: dict
    prepared_execution: dict
    intent_payload: dict
    spec_payload: dict
    user_id: str | None
    session_id: str | None
    expires_at: datetime
    error_code: str | None = None
    error_message: str | None = None
