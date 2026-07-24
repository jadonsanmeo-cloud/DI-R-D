"""Engine contracts and built-in engine placeholders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from data_intelligence_sdk.core.types import EngineInput, EngineOutput
from data_intelligence_sdk.engines.base import Engine

if TYPE_CHECKING:  # pragma: no cover - import-time convenience only.
    from data_intelligence_sdk.engines.general import GeneralPurposeEngine

__all__ = ["Engine", "EngineInput", "EngineOutput", "GeneralPurposeEngine"]


def __getattr__(name: str) -> object:
    if name == "GeneralPurposeEngine":
        from data_intelligence_sdk.engines.general import GeneralPurposeEngine

        return GeneralPurposeEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
