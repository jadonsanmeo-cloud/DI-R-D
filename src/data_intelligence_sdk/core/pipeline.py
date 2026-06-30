"""High-level orchestration boundary for the architecture flow."""

from __future__ import annotations

from data_intelligence_sdk.core.types import (
    DataHubContext,
    FinalResponse,
    SessionContext,
    UserContext,
    UserQuery,
)


class DataIntelligencePipeline:
    """Coordinates the major platform components.

    The base version only defines the composition point. Concrete behavior will
    be added as the SDK boundaries become clearer.
    """

    def __init__(
        self,
        *,
        intent_analyzer: object,
        spec_builder: object,
        spec_confirmation: object,
        engine_registry: object,
        evidence_collector: object,
        synthesizer: object,
    ) -> None:
        self.intent_analyzer = intent_analyzer
        self.spec_builder = spec_builder
        self.spec_confirmation = spec_confirmation
        self.engine_registry = engine_registry
        self.evidence_collector = evidence_collector
        self.synthesizer = synthesizer

    def run(
        self,
        query: UserQuery,
        datahub: DataHubContext,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> FinalResponse:
        """Run the full data intelligence flow.

        This method is intentionally not implemented yet. The package currently
        defines architecture boundaries, not execution behavior.
        """

        raise NotImplementedError("Pipeline execution will be implemented after the base contracts settle.")
