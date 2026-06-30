"""Runtime logging boundary."""

from __future__ import annotations

from typing import Any


class RuntimeLogger:
    """Collects structured runtime events."""

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        raise NotImplementedError("Logging backend is not part of the base scaffold.")
