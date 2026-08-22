"""Engine registration and selection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from data_intelligence_sdk.core.errors import EngineSelectionError
from data_intelligence_sdk.engines.base import Engine
from data_intelligence_sdk.registry.engine_selector import (
    EngineDescriptor,
    EngineSelector,
    EngineSelectionRequest,
)


class EngineRegistry(Protocol):
    """Selects an engine that can satisfy an execution spec."""

    def register(self, engine: Engine) -> None:
        """Register an engine implementation."""

    def resolve(
        self,
        request: EngineSelectionRequest,
        *,
        explicit_engine: str | None = None,
    ) -> "SelectedEngine":
        """Return the explicit or automatically selected engine."""


@dataclass(frozen=True, slots=True)
class SelectedEngine:
    """A catalog engine together with the source of its selection."""

    engine: Engine
    selection_source: Literal["explicit", "auto"]


class InMemoryEngineRegistry:
    """In-memory engine registry that delegates selection when configured."""

    def __init__(self, *, selector: EngineSelector | None = None) -> None:
        self._engines: dict[str, Engine] = {}
        self._selector = selector

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

    def resolve(
        self,
        request: EngineSelectionRequest,
        *,
        explicit_engine: str | None = None,
    ) -> SelectedEngine:
        """Resolve explicit overrides or one valid LLM selection."""

        if explicit_engine is not None:
            return SelectedEngine(
                engine=self._registered_engine(explicit_engine),
                selection_source="explicit",
            )

        if self._selector is None:
            raise EngineSelectionError("Automatic engine selection is not configured.")
        try:
            selected_name = self._selector.select(request, self.descriptors())
        except Exception as exc:
            raise EngineSelectionError("Automatic engine selection failed.") from exc
        return SelectedEngine(
            engine=self._registered_engine(selected_name),
            selection_source="auto",
        )

    def _registered_engine(self, name: str) -> Engine:
        selected = self._engines.get(name)
        if selected is None:
            raise EngineSelectionError(f"Selected engine is not registered: {name!r}")
        return selected
