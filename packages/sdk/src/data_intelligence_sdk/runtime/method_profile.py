"""Explicit allow-lists for the methods exposed by a Method Hub."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Iterable

from data_intelligence_sdk.runtime.method_hub import MethodHub

__all__ = [
    "MethodProfileError",
    "DEFAULT_METHODS",
    "load_method_names",
    "filter_method_hub",
]


DEFAULT_METHODS = (
    "inspect_data_folder",
    "profile_delimited_file",
    "summarize_delimited_columns",
    "aggregate_delimited_file",
    "filter_delimited_rows",
    "search_text_files",
    "summarize_wide_numeric_table",
    "scan_csv",
    "filter_csv",
    "sum_csv",
    "count_csv",
    "inspect_postgres_table",
    "inspect_postgres_tables",
    "count_postgres_tables",
    "aggregate_postgres_table",
)


class MethodProfileError(ValueError):
    """Raised when a Method Hub profile is invalid."""


def _normalize_names(values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise MethodProfileError("method_hub.enabled_methods must be a list of strings.")
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise MethodProfileError(
                "method_hub.enabled_methods must contain only non-empty strings."
            )
        name = value.strip()
        if name not in seen:
            names.append(name)
            seen.add(name)
    if not names:
        raise MethodProfileError("method_hub.enabled_methods must not be empty.")
    return tuple(names)


def load_method_names(path: str | Path | None = None) -> tuple[str, ...]:
    """Load the configured allow-list, with environment overrides."""

    configured_path = os.getenv("METHOD_HUB_CONFIG_PATH") or path
    names: tuple[str, ...]
    if configured_path:
        profile_path = Path(configured_path)
        if not profile_path.exists():
            raise MethodProfileError(f"Method Hub profile does not exist: {profile_path}")
        try:
            payload = tomllib.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MethodProfileError(f"Could not read Method Hub profile {profile_path}: {exc}") from exc
        section = payload.get("method_hub")
        if not isinstance(section, dict):
            raise MethodProfileError(f"{profile_path}: missing [method_hub] section.")
        names = _normalize_names(section.get("enabled_methods"))
    else:
        names = DEFAULT_METHODS

    override = os.getenv("METHOD_HUB_METHODS")
    if override:
        names = _normalize_names(override.split(","))
    if os.getenv("ENABLE_VECTOR_METHODS", "false").strip().lower() in {"1", "true", "yes", "on"}:
        names = tuple(dict.fromkeys((*names, "search_vector_chunks", "inspect_vector_chunks", "get_vector_stats")))
    return names


def filter_method_hub(method_hub: MethodHub, enabled_names: Iterable[str]) -> None:
    """Keep only configured methods and fail if the profile names are unknown."""

    enabled = tuple(enabled_names)
    available = {method.name for method in method_hub.list_methods()}
    unknown = [name for name in enabled if name not in available]
    if unknown:
        raise MethodProfileError(
            f"Method Hub profile references unknown method(s): {', '.join(unknown)}. "
            f"Available methods: {', '.join(sorted(available))}."
        )
    for method in method_hub.list_methods():
        if method.name not in enabled:
            method_hub.remove(method.name)
