"""Optional LangSmith tracing helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def langsmith_tracing_enabled() -> bool:
    """Return whether explicit LangSmith tracing is enabled.

    Prefer LangSmith's current environment-variable name while retaining the
    legacy LangChain spelling for existing deployments.  An explicitly set
    current value takes precedence, including when it disables tracing.
    """

    configured_value = os.getenv("LANGSMITH_TRACING")
    if configured_value is None:
        configured_value = os.getenv("LANGCHAIN_TRACING_V2", "")
    return configured_value.strip().lower() in {
        "true",
    }


def traceable_llm_call(
    function: F,
    *,
    name: str,
    reduce_fn: Callable[[list[Any]], Any] | None = None,
) -> F:
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
        project_name=(
            os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or None
        ),
        reduce_fn=reduce_fn,
    )(function)
