"""Concrete CSV MethodHub methods."""

from __future__ import annotations

import csv

from data_intelligence_sdk.runtime.method_hub import MethodHub

_SUPPORTED_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}


def _read_rows(path: str) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = list(reader.fieldnames or [])
        return columns, list(reader)


def _require_column(columns: list[str], column: str) -> None:
    if column not in columns:
        raise ValueError(f"CSV column not found: {column}")


def _coerce_number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Value is not numeric: {value}") from exc


def _matches(row: dict[str, str], column: str, op: str, value: object) -> bool:
    if op not in _SUPPORTED_OPS:
        raise ValueError(f"Unsupported CSV filter operator: {op}")

    left = row[column]
    if op == "eq":
        return left == str(value)
    if op == "ne":
        return left != str(value)
    if op == "contains":
        return str(value) in left

    left_number = _coerce_number(left)
    right_number = _coerce_number(value)
    if op == "gt":
        return left_number > right_number
    if op == "gte":
        return left_number >= right_number
    if op == "lt":
        return left_number < right_number
    if op == "lte":
        return left_number <= right_number
    return False


def scan_csv(path: str, limit: int = 10) -> dict[str, object]:
    """Return CSV columns, row count, and sample rows."""

    columns, rows = _read_rows(path)
    return {
        "path": path,
        "columns": columns,
        "row_count": len(rows),
        "sample_rows": rows[:limit],
    }


def filter_csv(path: str, column: str, op: str, value: object) -> dict[str, object]:
    """Return CSV rows matching a simple predicate."""

    columns, rows = _read_rows(path)
    _require_column(columns, column)
    matches = [row for row in rows if _matches(row, column, op, value)]
    return {
        "path": path,
        "column": column,
        "op": op,
        "value": value,
        "rows": matches,
        "count": len(matches),
    }


def count_csv(
    path: str,
    column: str | None = None,
    op: str | None = None,
    value: object | None = None,
) -> dict[str, object]:
    """Count all rows or rows matching a predicate."""

    if column is None:
        _, rows = _read_rows(path)
        return {"path": path, "count": len(rows)}
    if op is None:
        raise ValueError("CSV count filter requires an operator.")
    result = filter_csv(path, column, op, value)
    return {"path": path, "count": result["count"]}


def sum_csv(
    path: str,
    column: str,
    filter_column: str | None = None,
    filter_op: str | None = None,
    filter_value: object | None = None,
) -> dict[str, object]:
    """Sum a numeric CSV column, optionally after filtering."""

    columns, rows = _read_rows(path)
    _require_column(columns, column)
    if filter_column is not None:
        _require_column(columns, filter_column)
        if filter_op is None:
            raise ValueError("CSV sum filter requires an operator.")
        rows = [
            row for row in rows if _matches(row, filter_column, filter_op, filter_value)
        ]

    total = 0.0
    for row in rows:
        total += _coerce_number(row[column])
    return {"path": path, "column": column, "sum": total, "count": len(rows)}


def register_csv_methods(method_hub: MethodHub) -> None:
    """Register CSV methods with capability metadata for engine discovery."""

    common = ["answer_csv_question"]
    method_hub.register(
        "scan_csv",
        scan_csv,
        capability_names=["scan_csv", "inspect_data", *common],
        tags=["csv", "tabular", "inspection"],
        status="stable",
        priority=100,
        source=__name__,
        metadata={
            "description": "Inspect a CSV file before answering questions about its columns, row count, or sample rows.",
            "category": "csv",
            "deterministic": True,
            "side_effects": False,
            "use_when": [
                "You need a quick overview of a CSV file.",
                "You want columns, row counts, or sample rows.",
            ],
            "do_not_use_when": [
                "You already know the exact rows to aggregate without inspecting the file.",
            ],
        },
    )
    method_hub.register(
        "filter_csv",
        filter_csv,
        capability_names=["filter_csv", "filter_data", *common],
        tags=["csv", "tabular", "filtering"],
        status="stable",
        priority=90,
        source=__name__,
        metadata={
            "description": "Filter CSV rows with eq/ne/gt/gte/lt/lte/contains when a question asks for matching records.",
            "category": "csv",
            "deterministic": True,
            "side_effects": False,
            "use_when": [
                "You need a subset of rows that match a predicate.",
                "The user asks for rows matching a condition.",
            ],
            "do_not_use_when": [
                "You only need a total count or sum without row-level filtering.",
            ],
        },
    )
    method_hub.register(
        "count_csv",
        count_csv,
        capability_names=["count_csv", "aggregate_data", *common],
        tags=["csv", "tabular", "aggregation", "count"],
        status="stable",
        priority=80,
        source=__name__,
        metadata={
            "description": "Count CSV rows, optionally with a simple predicate.",
            "category": "csv",
            "deterministic": True,
            "side_effects": False,
            "use_when": [
                "You need row counts or record totals.",
                "The user asks how many records match a condition.",
            ],
            "do_not_use_when": [
                "You need to sum or average numeric values instead of counting rows.",
            ],
        },
    )
    method_hub.register(
        "sum_csv",
        sum_csv,
        capability_names=["sum_csv", "aggregate_data", *common],
        tags=["csv", "tabular", "aggregation", "sum"],
        status="stable",
        priority=85,
        source=__name__,
        metadata={
            "description": "Sum a numeric CSV column, optionally after filtering.",
            "category": "csv",
            "deterministic": True,
            "side_effects": False,
            "use_when": [
                "You need totals or revenue sums from a numeric column.",
                "The user asks for a sum or total value.",
            ],
            "do_not_use_when": [
                "You only need to count rows without aggregating a numeric column.",
            ],
        },
    )
