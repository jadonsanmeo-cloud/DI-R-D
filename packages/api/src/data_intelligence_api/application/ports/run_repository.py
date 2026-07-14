"""Persistence port for resumable response runs."""

from __future__ import annotations

from typing import Literal, Protocol

from data_intelligence_api.domain.runs import StoredRun


class RunRepository(Protocol):
    def create_pending(self, **kwargs) -> StoredRun: ...

    def get_authorized(self, response_id: str, token: str) -> StoredRun: ...

    def claim(
        self,
        response_id: str,
        token: str,
        *,
        revision: int,
        target_status: Literal["revising", "executing"],
    ) -> StoredRun: ...

    def save_revision(self, response_id: str, **kwargs) -> StoredRun: ...

    def record_confirmation(self, response_id: str, revision: int) -> None: ...

    def mark_completed(self, response_id: str) -> None: ...

    def mark_failed(self, response_id: str, code: str, message: str) -> None: ...

    def check_ready(self) -> bool: ...
