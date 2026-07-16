"""Compatibility exports for spec data selection agents.

The implementation lives in :mod:`data_intelligence_sdk.spec.data_selection`.
"""

from data_intelligence_sdk.spec.data_selection.selector import (
    DataSelector,
    LLMDataSelector,
)

__all__ = ["DataSelector", "LLMDataSelector"]
