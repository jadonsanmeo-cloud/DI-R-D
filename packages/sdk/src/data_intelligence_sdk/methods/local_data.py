"""General MethodHub methods for repository-local data folders."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from data_intelligence_sdk.runtime.method_hub import MethodHub

_TABLE_SUFFIXES = {".csv", ".tsv"}
_TEXT_SUFFIXES = {".md", ".txt"}
_DOCUMENT_SUFFIXES = {".md", ".pdf", ".txt"}
_MISSING_VALUES = {"", "n/a", "na", "null", "none", "-"}


def _resolve_data_root(data_root: str | Path = "data") -> Path:
    path = Path(data_root)
    if path.exists():
        return path
    repo_relative = Path(__file__).resolve().parents[3] / path
    if repo_relative.exists():
        return repo_relative
    return path


def _data_files(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("*")) if path.is_file()]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_file(
    path: str | Path | None,
    data_root: str | Path,
    suffixes: set[str],
) -> Path:
    root = _resolve_data_root(data_root)
    if path is not None:
        candidate = Path(path)
        if candidate.exists():
            return candidate
        rooted = root / candidate
        if rooted.exists():
            return rooted
        return candidate

    candidates = [
        file_path
        for file_path in _data_files(root)
        if file_path.suffix.lower() in suffixes
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No files with suffixes {sorted(suffixes)} under {root}."
        )
    return max(candidates, key=lambda item: item.stat().st_size)


def _detect_delimiter(path: Path) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        return "\t" if first_line.count("\t") > first_line.count(",") else ","


def _read_delimited(path: Path) -> tuple[str, list[str], list[dict[str, str]]]:
    delimiter = _detect_delimiter(path)
    with path.open(newline="", encoding="utf-8-sig") as table_file:
        reader = csv.DictReader(table_file, delimiter=delimiter)
        columns = [str(column) for column in (reader.fieldnames or [])]
        return delimiter, columns, list(reader)


def _number(value: object) -> float | None:
    text = str(value).strip().replace(",", "")
    if text.casefold() in _MISSING_VALUES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _numeric_values(rows: list[dict[str, str]], column: str) -> list[float]:
    values = [_number(row.get(column)) for row in rows]
    return [value for value in values if value is not None]


def _looks_numeric(rows: list[dict[str, str]], column: str) -> bool:
    non_missing = [
        row.get(column)
        for row in rows[:1000]
        if str(row.get(column, "")).strip().casefold() not in _MISSING_VALUES
    ]
    if not non_missing:
        return False
    numeric_count = sum(_number(value) is not None for value in non_missing)
    return numeric_count / len(non_missing) >= 0.8


def _column_profiles(
    rows: list[dict[str, str]], columns: list[str]
) -> list[dict[str, Any]]:
    profiles = []
    row_count = len(rows)
    for column in columns:
        values = [row.get(column, "") for row in rows]
        missing_count = sum(
            str(value).strip().casefold() in _MISSING_VALUES for value in values
        )
        numeric = _numeric_values(rows, column)
        profile: dict[str, Any] = {
            "column": column,
            "row_count": row_count,
            "missing_count": missing_count,
            "distinct_count": len(
                {str(value) for value in values if str(value).strip()}
            ),
            "is_numeric": bool(numeric) and _looks_numeric(rows, column),
        }
        if numeric:
            profile.update(
                {
                    "numeric_count": len(numeric),
                    "min": min(numeric),
                    "max": max(numeric),
                    "average": sum(numeric) / len(numeric),
                }
            )
        profiles.append(profile)
    return profiles


def _default_group_column(rows: list[dict[str, str]], columns: list[str]) -> str | None:
    best_column = None
    best_score = -1
    row_count = max(1, len(rows))
    for column in columns:
        if _looks_numeric(rows, column):
            continue
        distinct = len(
            {
                str(row.get(column, ""))
                for row in rows
                if str(row.get(column, "")).strip()
            }
        )
        if 1 < distinct <= min(50, row_count):
            score = row_count - distinct
            if score > best_score:
                best_column = column
                best_score = score
    return best_column or (columns[0] if columns else None)


def inspect_data_folder(data_root: str = "data", limit: int = 200) -> dict[str, Any]:
    """Inventory files in a data folder without assuming a domain-specific schema."""

    root = _resolve_data_root(data_root)
    rows = []
    for path in _data_files(root)[:limit]:
        relative = _relative(path, root)
        suffix = path.suffix.lower()
        if suffix in _TABLE_SUFFIXES:
            kind = "table"
        elif suffix in _DOCUMENT_SUFFIXES:
            kind = "document"
        elif suffix in {".xlsx", ".xls"}:
            kind = "spreadsheet"
        else:
            kind = "other"
        rows.append(
            {
                "relative_path": relative,
                "kind": kind,
                "file_type": suffix.lstrip(".") or "unknown",
                "directory": (
                    path.parent.relative_to(root).as_posix()
                    if path.parent != root
                    else "."
                ),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "1.0",
        "data_root": str(root),
        "file_count": len(_data_files(root)),
        "returned_file_count": len(rows),
        "rows": rows,
    }


def profile_delimited_file(
    path: str | None = None,
    data_root: str = "data",
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Profile any CSV/TSV file by columns, row count, missingness, and sample rows."""

    root = _resolve_data_root(data_root)
    table_path = _resolve_file(path, root, _TABLE_SUFFIXES)
    delimiter, columns, rows = _read_delimited(table_path)
    profiles = _column_profiles(rows, columns)
    return {
        "schema_version": "1.0",
        "source": str(table_path),
        "relative_path": _relative(table_path, root),
        "delimiter": "\\t" if delimiter == "\t" else delimiter,
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": columns,
        "numeric_columns": [
            profile["column"] for profile in profiles if profile["is_numeric"]
        ],
        "categorical_columns": [
            profile["column"] for profile in profiles if not profile["is_numeric"]
        ],
        "rows": profiles,
        "sample_rows": rows[:sample_limit],
    }


def summarize_delimited_columns(
    path: str | None = None,
    columns: list[str] | None = None,
    data_root: str = "data",
    top_limit: int = 12,
) -> dict[str, Any]:
    """Summarize selected columns in any CSV/TSV with top values and numeric stats."""

    root = _resolve_data_root(data_root)
    table_path = _resolve_file(path, root, _TABLE_SUFFIXES)
    delimiter, available_columns, rows = _read_delimited(table_path)
    selected = columns or available_columns
    selected = [column for column in selected if column in available_columns]
    result_rows: list[dict[str, Any]] = []
    for column in selected:
        values = [row.get(column, "") for row in rows]
        numeric = _numeric_values(rows, column)
        if numeric:
            result_rows.append(
                {
                    "record_type": "numeric_summary",
                    "column": column,
                    "numeric_count": len(numeric),
                    "min": min(numeric),
                    "max": max(numeric),
                    "average": sum(numeric) / len(numeric),
                }
            )
        counts = Counter(str(value) or "Missing" for value in values)
        for value, count in counts.most_common(top_limit):
            result_rows.append(
                {
                    "record_type": "top_value",
                    "column": column,
                    "value": value,
                    "count": count,
                }
            )
    return {
        "schema_version": "1.0",
        "source": str(table_path),
        "relative_path": _relative(table_path, root),
        "delimiter": "\\t" if delimiter == "\t" else delimiter,
        "row_count": len(rows),
        "summarized_columns": selected,
        "rows": result_rows,
    }


def aggregate_delimited_file(
    path: str | None = None,
    group_by: str | None = None,
    metric_column: str | None = None,
    aggregation: str = "count",
    data_root: str = "data",
    top_limit: int = 50,
) -> dict[str, Any]:
    """Group a CSV/TSV by one column and compute count, sum, average, min, or max."""

    root = _resolve_data_root(data_root)
    table_path = _resolve_file(path, root, _TABLE_SUFFIXES)
    _, columns, rows = _read_delimited(table_path)
    group_column = (
        group_by if group_by in columns else _default_group_column(rows, columns)
    )
    if group_column is None:
        return {"schema_version": "1.0", "source": str(table_path), "rows": []}
    if aggregation != "count" and (
        metric_column is None or metric_column not in columns
    ):
        numeric_columns = [column for column in columns if _looks_numeric(rows, column)]
        metric_column = numeric_columns[0] if numeric_columns else None
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_column, "")) or "Missing"].append(row)

    result_rows = []
    for group_value, group_rows in grouped.items():
        output: dict[str, Any] = {
            "group_by": group_column,
            "group_value": group_value,
            "row_count": len(group_rows),
        }
        numeric = _numeric_values(group_rows, metric_column) if metric_column else []
        if aggregation == "sum":
            output["value"] = sum(numeric)
        elif aggregation == "average":
            output["value"] = sum(numeric) / len(numeric) if numeric else None
        elif aggregation == "min":
            output["value"] = min(numeric) if numeric else None
        elif aggregation == "max":
            output["value"] = max(numeric) if numeric else None
        else:
            output["value"] = len(group_rows)
        output["aggregation"] = aggregation
        output["metric_column"] = metric_column
        result_rows.append(output)

    result_rows.sort(
        key=lambda row: (
            -(
                row["value"]
                if isinstance(row["value"], (int, float))
                else row["row_count"]
            ),
            str(row["group_value"]),
        )
    )
    return {
        "schema_version": "1.0",
        "source": str(table_path),
        "relative_path": _relative(table_path, root),
        "group_by": group_column,
        "metric_column": metric_column,
        "aggregation": aggregation,
        "rows": result_rows[:top_limit],
    }


def filter_delimited_rows(
    path: str | None = None,
    query: str = "",
    column: str | None = None,
    contains: str | None = None,
    data_root: str = "data",
    limit: int = 25,
) -> dict[str, Any]:
    """Return rows from a CSV/TSV that match text terms in one or all columns."""

    root = _resolve_data_root(data_root)
    table_path = _resolve_file(path, root, _TABLE_SUFFIXES)
    _, columns, rows = _read_delimited(table_path)
    terms = [term.casefold() for term in (contains or query).split() if term.strip()]
    matched = []
    for row in rows:
        haystack_values = [row.get(column, "")] if column in columns else row.values()
        haystack = " ".join(str(value) for value in haystack_values).casefold()
        if not terms or all(term in haystack for term in terms):
            matched.append(row)
        if len(matched) >= limit:
            break
    return {
        "schema_version": "1.0",
        "source": str(table_path),
        "relative_path": _relative(table_path, root),
        "query": contains or query,
        "column": column,
        "matched_count_returned": len(matched),
        "rows": matched,
    }


def search_text_files(
    query: str = "",
    data_root: str = "data",
    glob_pattern: str = "**/*.md",
    limit: int = 8,
) -> dict[str, Any]:
    """Search text/markdown files and return document-level snippets."""

    root = _resolve_data_root(data_root)
    terms = [term.casefold() for term in query.split() if len(term) > 2]
    rows = []
    for path in sorted(root.glob(glob_pattern)):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lower_text = text.casefold()
        score = sum(lower_text.count(term) for term in terms) if terms else 0
        if terms and score == 0:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        rows.append(
            {
                "document": path.name,
                "relative_path": _relative(path, root),
                "score": score,
                "line_count": len(text.splitlines()),
                "character_count": len(text),
                "snippet": " ".join(lines[:6])[:700],
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["relative_path"])))
    return {
        "schema_version": "1.0",
        "data_root": str(root),
        "query": query,
        "matched_document_count": len(rows),
        "rows": rows[:limit],
    }


def summarize_wide_numeric_table(
    path: str | None = None,
    id_column: str | None = None,
    data_root: str = "data",
    top_limit: int = 50,
) -> dict[str, Any]:
    """Summarize a wide table whose non-id columns are mostly numeric measures."""

    root = _resolve_data_root(data_root)
    table_path = Path(path) if path is not None else _select_wide_numeric_table(root)
    if path is not None and not table_path.exists():
        table_path = root / table_path
    _, columns, rows = _read_delimited(table_path)
    if not rows:
        return {"schema_version": "1.0", "source": str(table_path), "rows": []}
    identity = id_column if id_column in columns else columns[0]
    measure_columns = [
        column
        for column in columns
        if column != identity and _looks_numeric(rows, column)
    ]
    result_rows = []
    for row in rows:
        values = {
            column: _number(row.get(column))
            for column in measure_columns
            if _number(row.get(column)) is not None
        }
        if not values:
            continue
        top_measure = max(values, key=lambda column: values[column])
        result_rows.append(
            {
                "id_column": identity,
                "id_value": row.get(identity),
                "measure_count": len(values),
                "measure_total": sum(values.values()),
                "top_measure": top_measure,
                "top_measure_value": values[top_measure],
            }
        )
    result_rows.sort(key=lambda row: -(row["measure_total"] or 0))
    return {
        "schema_version": "1.0",
        "source": str(table_path),
        "relative_path": _relative(table_path, root),
        "id_column": identity,
        "measure_columns": measure_columns,
        "rows": result_rows[:top_limit],
    }


def _select_wide_numeric_table(root: Path) -> Path:
    candidates = [
        path for path in _data_files(root) if path.suffix.lower() in _TABLE_SUFFIXES
    ]
    scored = []
    for path in candidates:
        try:
            _, columns, rows = _read_delimited(path)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        numeric_columns = [column for column in columns if _looks_numeric(rows, column)]
        direct_child_bonus = 10 if path.parent == root else 0
        compact_bonus = 5 if len(rows) <= 1000 else 0
        score = len(numeric_columns) + direct_child_bonus + compact_bonus
        scored.append((score, path.stat().st_size, path))
    if not scored:
        raise FileNotFoundError(f"No delimited table files under {root}.")
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].as_posix()))
    return scored[0][2]


def register_local_data_methods(method_hub: MethodHub) -> None:
    """Register general methods for local data-folder inspection and reporting."""

    common_metadata = {
        "category": "local_data",
        "deterministic": True,
        "side_effects": False,
    }
    definitions = [
        (
            "inspect_data_folder",
            inspect_data_folder,
            [
                "inspect_data",
                "inspect_local_data",
                "data_folder_report",
                "generate_report",
            ],
            "Inventory files in a data folder and classify tables, documents, spreadsheets, and other assets.",
            ["local-data", "inventory", "report"],
            150,
            [
                "The report needs an overview of available local files.",
                "The user asks what information is present in a data folder.",
            ],
        ),
        (
            "profile_delimited_file",
            profile_delimited_file,
            [
                "inspect_data",
                "profile_data",
                "inspect_tabular_data",
                "data_folder_report",
            ],
            "Profile any CSV or TSV file by row count, columns, missingness, numeric columns, and sample rows.",
            ["local-data", "table", "profile"],
            145,
            [
                "The report needs schema, sample rows, column types, or data quality for a local table.",
            ],
        ),
        (
            "summarize_delimited_columns",
            summarize_delimited_columns,
            [
                "aggregate_data",
                "summarize_columns",
                "inspect_tabular_data",
                "data_folder_report",
            ],
            "Summarize selected CSV/TSV columns with top values and numeric statistics.",
            ["local-data", "table", "summary"],
            140,
            [
                "The report needs top categories, value distributions, or numeric ranges from a local table.",
            ],
        ),
        (
            "aggregate_delimited_file",
            aggregate_delimited_file,
            ["aggregate_data", "group_data", "summarize_metrics", "data_folder_report"],
            "Group any CSV/TSV by a column and compute count, sum, average, min, or max.",
            ["local-data", "table", "aggregation"],
            138,
            [
                "The report needs grouped counts or metrics from a local table.",
            ],
        ),
        (
            "filter_delimited_rows",
            filter_delimited_rows,
            ["filter_data", "search_table_rows", "data_folder_report"],
            "Return CSV/TSV rows matching text terms in one or all columns.",
            ["local-data", "table", "search"],
            134,
            [
                "The report needs row-level examples matching a keyword or phrase.",
            ],
        ),
        (
            "search_text_files",
            search_text_files,
            [
                "search_documents",
                "inspect_documents",
                "data_folder_report",
                "generate_report",
            ],
            "Search markdown or text files and return document snippets with match scores.",
            ["local-data", "documents", "markdown", "search"],
            132,
            [
                "The report needs context or evidence from local text or markdown documents.",
            ],
        ),
        (
            "summarize_wide_numeric_table",
            summarize_wide_numeric_table,
            [
                "aggregate_data",
                "summarize_wide_table",
                "summarize_metrics",
                "data_folder_report",
            ],
            "Summarize a wide numeric table by row identifier, totals, and top numeric measure.",
            ["local-data", "table", "wide-table", "summary"],
            130,
            [
                "The report needs totals across many numeric measure columns in a compact wide table.",
            ],
        ),
    ]
    for (
        name,
        method,
        capabilities,
        description,
        tags,
        priority,
        use_when,
    ) in definitions:
        method_hub.register(
            name,
            method,
            capability_names=capabilities,
            trust_level="builtin",
            metadata={
                **common_metadata,
                "description": description,
                "use_when": use_when,
            },
            version="1.0.0",
            description=description,
            tags=tags,
            status="stable",
            priority=priority,
            source=__name__,
        )
