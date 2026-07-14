"""Application task broker port and local in-process adapter."""

from __future__ import annotations

from typing import Any, Protocol


class TaskBroker(Protocol):
    async def publish(self, task_name: str, payload: dict[str, Any]) -> None: ...
