"""Base engine contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
)


class Engine(Protocol):
    """Executable unit selected by the engine registry."""

    @property
    def name(self) -> str:
        """Stable engine identifier."""

    @property
    def description(self) -> str:
        """Human-readable routing description."""

    def run(
        self,
        input: EngineInput,
    ) -> EngineOutput:
        """Execute the spec and return output with structured trace."""


__all__ = ["Engine", "EngineInput", "EngineOutput"]
