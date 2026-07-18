"""PostgreSQL migration and readiness helpers."""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATION_LOCK_ID = 731_420_117


def run_migrations(database_url: str, migrations_dir: Path | None = None) -> None:
    directory = migrations_dir or Path(__file__).with_name("migrations")
    migrations = sorted(directory.glob("*.sql"))
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """)
                for migration in migrations:
                    cursor.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %s",
                        (migration.name,),
                    )
                    if cursor.fetchone() is not None:
                        continue
                    cursor.execute(migration.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)",
                        (migration.name,),
                    )
                connection.commit()
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
