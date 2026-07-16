"""Local task broker adapter for development and tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class InProcessTaskBroker:
    """Dispatches tasks locally until a RabbitMQ adapter is configured."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def register(
        self,
        task_name: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.handlers[task_name] = handler

    async def publish(self, task_name: str, payload: dict[str, Any]) -> None:
        handler = self.handlers.get(task_name)
        if handler is None:
            raise ValueError(f"No in-process handler registered for {task_name!r}.")
        await handler(payload)
