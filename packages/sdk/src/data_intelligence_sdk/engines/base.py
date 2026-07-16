"""Base engine contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class Engine(Protocol):
    """Executable unit selected by the engine registry."""

    @property
    def name(self) -> str:
        """Stable engine identifier."""

    def can_handle(self, spec: ExecutionSpec) -> bool:
        """Return whether this engine is suitable for a spec."""

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        """Execute the spec and return output with structured trace."""
