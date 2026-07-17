"""Trusted read-only PostgreSQL MethodHub methods."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

from data_intelligence_sdk.runtime.method_hub import MethodHub

ConnectionFactory = Callable[[str], Any]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGGREGATIONS = {"count", "sum", "avg", "min", "max"}


def _postgres_dsn(database: str) -> str:
    parsed = urlparse(database)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("PostgreSQL methods require a postgres/postgresql URI.")
    return urlunparse(parsed._replace(query=""))


def _identifier(value: object, label: str) -> str:
    rendered = str(value)
    if not _IDENTIFIER.fullmatch(rendered):
        raise ValueError(f"Invalid PostgreSQL {label}: {rendered}")
    return rendered


def _limit(value: int) -> int:
    return max(1, min(int(value), 200))


def _default_connection_factory(dsn: str) -> Any:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - project dependency in normal use.
        raise RuntimeError(
            "psycopg is required for PostgreSQL MethodHub methods."
        ) from exc
    return psycopg.connect(dsn)


def _row_dicts(cursor: Any, rows: list[object]) -> list[dict[str, Any]]:
    columns = [
        str(getattr(item, "name", item[0] if isinstance(item, tuple) else item))
        for item in cursor.description or []
    ]
    return [dict(zip(columns, row, strict=False)) for row in rows]


def inspect_postgres_table(
    database: str,
    table: str,
    columns: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Read a bounded sample from one validated PostgreSQL table."""

    return inspect_postgres_table_with_connection(
        database,
        table,
        columns=columns,
        limit=limit,
        connection_factory=None,
    )


def inspect_postgres_table_with_connection(
    database: str,
    table: str,
    columns: list[str] | None = None,
    limit: int = 20,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    table_name = _identifier(table, "table")
    selected_columns = [_identifier(item, "column") for item in columns or []]
    projection = ", ".join(f'"{item}"' for item in selected_columns) or "*"
    bounded_limit = _limit(limit)
    connect = connection_factory or _default_connection_factory

    with connect(_postgres_dsn(database)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT {projection} FROM "{table_name}" LIMIT %s',
                (bounded_limit,),
            )
            rows = cursor.fetchall()
            records = _row_dicts(cursor, rows)

    return {
        "database": database,
        "table": table_name,
        "columns": list(records[0]) if records else selected_columns,
        "row_count": len(records),
        "rows": records,
    }


def inspect_postgres_tables(
    database: str,
    tables: list[str],
    columns_by_table: dict[str, list[str]] | None = None,
    limit_per_table: int = 20,
) -> dict[str, Any]:
    """Read bounded samples from multiple validated PostgreSQL tables."""

    return inspect_postgres_tables_with_connection(
        database,
        tables,
        columns_by_table=columns_by_table,
        limit_per_table=limit_per_table,
        connection_factory=None,
    )


def inspect_postgres_tables_with_connection(
    database: str,
    tables: list[str],
    columns_by_table: dict[str, list[str]] | None = None,
    limit_per_table: int = 20,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    connect = connection_factory or _default_connection_factory
    dsn = _postgres_dsn(database)
    bounded_limit = _limit(limit_per_table)
    table_results = []
    combined_rows = []
    with connect(dsn) as connection:
        for table in tables:
            table_name = _identifier(table, "table")
            selected_columns = [
                _identifier(item, "column")
                for item in (columns_by_table or {}).get(table_name, [])
            ]
            projection = ", ".join(f'"{item}"' for item in selected_columns) or "*"
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT {projection} FROM "{table_name}" LIMIT %s',
                    (bounded_limit,),
                )
                records = _row_dicts(cursor, cursor.fetchall())
            table_results.append(
                {
                    "table": table_name,
                    "columns": list(records[0]) if records else selected_columns,
                    "row_count": len(records),
                    "rows": records,
                }
            )
            combined_rows.extend({"_table": table_name, **row} for row in records)
    return {
        "database": database,
        "table_count": len(table_results),
        "tables": table_results,
        "row_count": len(combined_rows),
        "rows": combined_rows,
    }


def count_postgres_tables(database: str, tables: list[str]) -> dict[str, Any]:
    """Return exact row counts for multiple validated PostgreSQL tables."""

    return count_postgres_tables_with_connection(
        database,
        tables,
        connection_factory=None,
    )


def count_postgres_tables_with_connection(
    database: str,
    tables: list[str],
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    connect = connection_factory or _default_connection_factory
    rows = []
    with connect(_postgres_dsn(database)) as connection:
        for table in tables:
            table_name = _identifier(table, "table")
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"', ())
                result = cursor.fetchone()
            rows.append(
                {
                    "table": table_name,
                    "row_count": int(result[0]) if result else 0,
                }
            )
    return {
        "database": database,
        "table_count": len(rows),
        "rows": rows,
    }


def aggregate_postgres_table(
    database: str,
    table: str,
    metrics: list[dict[str, Any]],
    group_by: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run bounded, validated group-by aggregations on one PostgreSQL table."""

    return aggregate_postgres_table_with_connection(
        database,
        table,
        metrics,
        group_by=group_by,
        filters=filters,
        limit=limit,
        connection_factory=None,
    )


def aggregate_postgres_table_with_connection(
    database: str,
    table: str,
    metrics: list[dict[str, Any]],
    group_by: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    table_name = _identifier(table, "table")
    groups = [_identifier(item, "group-by column") for item in group_by or []]
    metric_sql = []
    normalized_metrics = []
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise ValueError("PostgreSQL metrics must be objects.")
        aggregation = str(metric.get("aggregation", "")).lower()
        if aggregation not in _AGGREGATIONS:
            raise ValueError(f"Unsupported PostgreSQL aggregation: {aggregation}")
        field = metric.get("field")
        expression = (
            "*"
            if aggregation == "count" and not field
            else f'"{_identifier(field, "metric field")}"'
        )
        name = _identifier(
            metric.get("name") or f"{aggregation}_{index + 1}", "metric name"
        )
        metric_sql.append(f'{aggregation.upper()}({expression}) AS "{name}"')
        normalized_metrics.append(
            {"name": name, "field": field, "aggregation": aggregation}
        )
    if not metric_sql:
        metric_sql = ['COUNT(*) AS "record_count"']
        normalized_metrics = [
            {"name": "record_count", "field": None, "aggregation": "count"}
        ]

    filter_items = [
        (_identifier(name, "filter column"), value)
        for name, value in (filters or {}).items()
    ]
    select_items = [f'"{item}"' for item in groups] + metric_sql
    sql = f'SELECT {", ".join(select_items)} FROM "{table_name}"'
    params: list[Any] = []
    if filter_items:
        sql += " WHERE " + " AND ".join(f'"{name}" = %s' for name, _ in filter_items)
        params.extend(value for _, value in filter_items)
    if groups:
        sql += " GROUP BY " + ", ".join(f'"{item}"' for item in groups)
    sql += " LIMIT %s"
    params.append(_limit(limit))

    connect = connection_factory or _default_connection_factory
    with connect(_postgres_dsn(database)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            records = _row_dicts(cursor, cursor.fetchall())

    return {
        "database": database,
        "table": table_name,
        "group_by": groups,
        "metrics": normalized_metrics,
        "row_count": len(records),
        "rows": records,
    }


def register_postgres_methods(method_hub: MethodHub) -> None:
    """Register trusted, read-only PostgreSQL inspection and aggregation."""

    method_hub.register(
        "inspect_postgres_table",
        inspect_postgres_table,
        capability_names=["inspect_data", "query_structured_data", "summarize_corpus"],
        metadata={
            "description": (
                "Read a bounded sample from a validated table in a PostgreSQL corpus source."
            )
        },
    )
    method_hub.register(
        "inspect_postgres_tables",
        inspect_postgres_tables,
        capability_names=["inspect_data", "query_structured_data", "summarize_corpus"],
        metadata={
            "description": (
                "Read bounded samples from multiple validated tables in a PostgreSQL corpus."
            )
        },
    )
    method_hub.register(
        "count_postgres_tables",
        count_postgres_tables,
        capability_names=["inspect_data", "aggregate_data", "summarize_corpus"],
        metadata={
            "description": "Return exact row counts for validated PostgreSQL corpus tables."
        },
    )
    method_hub.register(
        "aggregate_postgres_table",
        aggregate_postgres_table,
        capability_names=["aggregate_data", "query_structured_data"],
        metadata={
            "description": (
                "Run validated count/sum/avg/min/max aggregations on a PostgreSQL table."
            )
        },
    )
