import unittest

from data_intelligence_sdk.methods.postgres import (
    aggregate_postgres_table_with_connection,
    count_postgres_tables_with_connection,
    inspect_postgres_table_with_connection,
    inspect_postgres_tables_with_connection,
    register_postgres_methods,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


class Column:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, rows, columns):
        self.rows = rows
        self.description = [Column(name) for name in columns]
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, rows, columns):
        self.cursor_obj = FakeCursor(rows, columns)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_obj


class PostgresMethodTests(unittest.TestCase):
    def test_registers_trusted_postgres_methods(self) -> None:
        hub = MethodHub()

        register_postgres_methods(hub)

        self.assertEqual(
            {method.name for method in hub.list_methods()},
            {
                "inspect_postgres_table",
                "inspect_postgres_tables",
                "count_postgres_tables",
                "aggregate_postgres_table",
            },
        )
        self.assertIn(
            "query_structured_data",
            hub.get_definition("inspect_postgres_table").capability_names,
        )

    def test_inspect_table_validates_identifiers_and_returns_records(self) -> None:
        connection = FakeConnection(
            [(1, "US", "enterprise")],
            ["customer_id", "country", "segment"],
        )

        result = inspect_postgres_table_with_connection(
            "postgresql://demo:demo@localhost:5432/data_corpus",
            "customers",
            columns=["customer_id", "country", "segment"],
            limit=10,
            connection_factory=lambda dsn: connection,
        )

        sql, params = connection.cursor_obj.executed[0]
        self.assertIn('FROM "customers"', sql)
        self.assertEqual(params, (10,))
        self.assertEqual(result["rows"][0]["country"], "US")

        with self.assertRaisesRegex(ValueError, "Invalid PostgreSQL table"):
            inspect_postgres_table_with_connection(
                "postgresql://demo/db",
                "customers; DROP TABLE customers",
                connection_factory=lambda dsn: connection,
            )

    def test_aggregate_table_uses_parameterized_filters(self) -> None:
        connection = FakeConnection(
            [("US", 30.0, 2)],
            ["country", "total_revenue", "order_count"],
        )

        result = aggregate_postgres_table_with_connection(
            "postgresql://demo/db",
            "orders",
            metrics=[
                {"name": "total_revenue", "field": "revenue", "aggregation": "sum"},
                {"name": "order_count", "field": None, "aggregation": "count"},
            ],
            group_by=["country"],
            filters={"status": "complete"},
            connection_factory=lambda dsn: connection,
        )

        sql, params = connection.cursor_obj.executed[0]
        self.assertIn('SUM("revenue")', sql)
        self.assertIn('"status" = %s', sql)
        self.assertEqual(params, ("complete", 100))
        self.assertEqual(result["rows"][0]["total_revenue"], 30.0)

    def test_inspect_multiple_tables_combines_bounded_rows(self) -> None:
        connection = FakeConnection(
            [(1, "US")],
            ["record_id", "country"],
        )

        result = inspect_postgres_tables_with_connection(
            "postgresql://demo/db",
            ["customers", "orders"],
            limit_per_table=5,
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(result["table_count"], 2)
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(
            [row["_table"] for row in result["rows"]],
            ["customers", "orders"],
        )
        self.assertEqual(len(connection.cursor_obj.executed), 2)

    def test_count_multiple_tables_returns_exact_counts(self) -> None:
        connection = FakeConnection([(7,)], ["count"])

        result = count_postgres_tables_with_connection(
            "postgresql://demo/db",
            ["customers", "orders"],
            connection_factory=lambda dsn: connection,
        )

        self.assertEqual(result["rows"], [
            {"table": "customers", "row_count": 7},
            {"table": "orders", "row_count": 7},
        ])
        self.assertEqual(len(connection.cursor_obj.executed), 2)


if __name__ == "__main__":
    unittest.main()
