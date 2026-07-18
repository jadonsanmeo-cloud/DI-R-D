"""Engine registration and selection contracts."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.errors import EngineNotFoundError
from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.engines.base import Engine
from data_intelligence_sdk.registry.engine_selector import (
    EngineDescriptor,
    EngineSelector,
)


class EngineRegistry(Protocol):
    """Selects an engine that can satisfy an execution spec."""

    def register(self, engine: Engine) -> None:
        """Register an engine implementation."""

    def select(self, spec: ExecutionSpec) -> Engine:
        """Return the engine selected for the spec."""


class InMemoryEngineRegistry:
    """In-memory engine registry with optional agent-backed selection."""

    def __init__(
        self,
        fallback_engine: Engine | None = None,
        *,
        selector: EngineSelector | None = None,
        fallback_engine_name: str | None = None,
    ) -> None:
        self._engines: dict[str, Engine] = {}
        self._fallback_engine = fallback_engine
        self._selector = selector
        self._fallback_engine_name = fallback_engine_name

    def set_fallback(self, engine: Engine | None) -> None:
        self._fallback_engine = engine
        self._fallback_engine_name = None

    def register(self, engine: Engine) -> None:
        self._engines[engine.name] = engine

    def descriptors(self) -> tuple[EngineDescriptor, ...]:
        """Return immutable selector metadata in registration order."""

        return tuple(
            EngineDescriptor(
                name=engine.name,
                description=engine.description,
            )
            for engine in self._engines.values()
        )

    def select(self, spec: ExecutionSpec) -> Engine:
        if self._selector is not None:
            try:
                selected_name = self._selector.select(spec, self.descriptors())
            except Exception:
                return self._fallback(spec)
            selected = self._engines.get(selected_name)
            return selected if selected is not None else self._fallback(spec)

        for engine in self._engines.values():
            if engine.can_handle(spec):
                return engine

        return self._fallback(spec)

    def _fallback(self, spec: ExecutionSpec) -> Engine:
        if self._fallback_engine_name is not None:
            fallback = self._engines.get(self._fallback_engine_name)
            if fallback is None:
                raise EngineNotFoundError(
                    "Configured fallback engine is not registered: "
                    f"{self._fallback_engine_name}"
                )
            return fallback

        if self._fallback_engine is not None:
            return self._fallback_engine

        raise EngineNotFoundError(
            f"No engine registered for spec objective: {spec.objective}"
        )
