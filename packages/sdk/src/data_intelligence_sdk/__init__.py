"""Base package for the Data Intelligence orchestration system."""

from __future__ import annotations

from typing import TYPE_CHECKING

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    EngineInput,
    EngineOutput,
    EngineStep,
    EngineTrace,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
    Intent,
    IntentAnalysis,
    InterfaceDefinition,
    InterfaceSource,
    MethodCall,
    MethodStatus,
    PreprocessingStep,
    SUPPORTED_INTENTS,
    SessionContext,
    TraceStatus,
    TrustLevel,
    UploadedFile,
    UserContext,
    UserQuery,
)

if TYPE_CHECKING:  # pragma: no cover - import-time convenience only.
    from data_intelligence_sdk.engines.general import GeneralPurposeEngine

__all__ = [
    "CapabilityRequirement",
    "EngineOutput",
    "EngineInput",
    "EngineStep",
    "EngineTrace",
    "EvidenceBundle",
    "ExecutionSpec",
    "FinalResponse",
    "GeneralPurposeEngine",
    "Intent",
    "IntentAnalysis",
    "InterfaceDefinition",
    "InterfaceSource",
    "MethodCall",
    "MethodStatus",
    "PreprocessingStep",
    "SUPPORTED_INTENTS",
    "SessionContext",
    "TraceStatus",
    "TrustLevel",
    "UploadedFile",
    "UserContext",
    "UserQuery",
]


def __getattr__(name: str) -> object:
    if name == "GeneralPurposeEngine":
        from data_intelligence_sdk.engines.general import GeneralPurposeEngine

        return GeneralPurposeEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), "GeneralPurposeEngine"})
