"""Optional LangSmith tracing helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def langsmith_tracing_enabled() -> bool:
    """Return whether explicit LangSmith tracing is enabled."""

    return os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower() in {
        "true",
    }


def traceable_llm_call(function: F, *, name: str) -> F:
    """Wrap a custom LLM call with LangSmith when tracing is enabled."""

    if not langsmith_tracing_enabled():
        return function
    try:
        from langsmith import traceable
    except ImportError:
        return function
    return traceable(
        name=name,
        run_type="llm",
        project_name=os.getenv("LANGCHAIN_PROJECT") or None,
    )(function)
