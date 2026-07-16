"""Engine registration and selection contracts."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.errors import EngineNotFoundError
from data_intelligence_sdk.core.types import ExecutionSpec
from data_intelligence_sdk.engines.base import Engine


class EngineRegistry(Protocol):
    """Selects an engine that can satisfy an execution spec."""

    def register(self, engine: Engine) -> None:
        """Register an engine implementation."""

    def select(self, spec: ExecutionSpec) -> Engine:
        """Return the engine selected for the spec."""


class InMemoryEngineRegistry:
    """In-memory engine registry with optional fallback selection."""

    def __init__(self, fallback_engine: Engine | None = None) -> None:
        self._engines: dict[str, Engine] = {}
        self._fallback_engine = fallback_engine

    def set_fallback(self, engine: Engine | None) -> None:
        self._fallback_engine = engine

    def register(self, engine: Engine) -> None:
        self._engines[engine.name] = engine

    def select(self, spec: ExecutionSpec) -> Engine:
        if spec.engine_hint and spec.engine_hint in self._engines:
            return self._engines[spec.engine_hint]

        for engine in self._engines.values():
            if engine.can_handle(spec):
                return engine

        if self._fallback_engine is not None:
            return self._fallback_engine

        raise EngineNotFoundError(
            f"No engine registered for spec objective: {spec.objective}"
        )
