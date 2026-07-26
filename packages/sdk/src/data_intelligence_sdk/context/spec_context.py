"""Compatibility exports for spec context builders.

The implementation lives in :mod:`data_intelligence_sdk.spec.context`.
"""

from data_intelligence_sdk.spec.context import (
    CorpusSummary,
    SessionBrief,
    SpecBuildContext,
    SpecContextBuilder,
    UserBrief,
    build_corpus_summary,
)

__all__ = [
    "CorpusSummary",
    "SessionBrief",
    "SpecBuildContext",
    "SpecContextBuilder",
    "UserBrief",
    "build_corpus_summary",
]
