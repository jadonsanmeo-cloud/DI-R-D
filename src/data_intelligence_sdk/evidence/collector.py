"""Evidence collection contract."""

from __future__ import annotations

from typing import Protocol

from data_intelligence_sdk.core.types import EngineOutput, EvidenceBundle, ExecutionSpec


class EvidenceCollector(Protocol):
    """Builds evidence from engine trace, observations, logs, and artifacts."""

    def collect(self, spec: ExecutionSpec, output: EngineOutput) -> EvidenceBundle:
        """Return evidence for synthesis and audit."""
