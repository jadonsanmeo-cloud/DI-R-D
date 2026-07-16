"""Final response synthesis contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import (
    EngineOutput,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
)


class Synthesizer(Protocol):
    """Turns engine output and evidence into a user-facing response."""

    def synthesize(
        self,
        spec: ExecutionSpec,
        output: EngineOutput,
        evidence: EvidenceBundle,
    ) -> FinalResponse:
        """Return the final response."""
