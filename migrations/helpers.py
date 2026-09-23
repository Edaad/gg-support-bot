"""Guarded DDL for Alembic revisions.

Each helper is a no-op when its target already exists (or is already gone), so any
revision can be re-run safely. Revisions must use these instead of raw ``op.*`` DDL
(enforced by ``scripts/check_migrations.py``).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


def _exec(sql: str) -> None:
    # Escape ':' so SQLAlchemy does not treat '::type' casts or literals as bind params.
    op.execute(text(sql.replace(":", r"\:")))


def _constraint_exists_sql(table: str, name: str) -> str:
    return (
        "SELECT 1 FROM pg_constraint "
        f"WHERE conname = '{name}' AND conrelid = to_regclass('{table}')"
    )


def create_sequence(name: str, options: str = "") -> None:
    _exec(f"CREATE SEQUENCE IF NOT EXISTS {name} {options}".rstrip())


def own_sequence(name: str, table: str, column: str) -> None:
    _exec(f"ALTER SEQUENCE {name} OWNED BY {table}.{column}")


def create_table(name: str, columns_sql: str) -> None:
    _exec(f"CREATE TABLE IF NOT EXISTS {name} (\n{columns_sql}\n)")


def drop_table(name: str) -> None:
    _exec(f"DROP TABLE IF EXISTS {name}")


def add_column(table: str, column: str, definition: str) -> None:
    _exec(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")


def drop_column(table: str, column: str) -> None:
    _exec(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")


def set_column_default(table: str, column: str, default_sql: str) -> None:
    _exec(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default_sql}")


def set_not_null(table: str, column: str) -> None:
    _exec(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")


def drop_not_null(table: str, column: str) -> None:
    _exec(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL")


def add_constraint(table: str, name: str, definition: str) -> None:
    _exec(
        "DO $$ BEGIN\n"
        f"IF NOT EXISTS ({_constraint_exists_sql(table, name)}) THEN\n"
        f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition};\n"
        "END IF;\n"
        "END $$"
    )


def drop_constraint(table: str, name: str) -> None:
    _exec(f"ALTER TABLE IF EXISTS {table} DROP CONSTRAINT IF EXISTS {name}")


def create_index(name: str, table: str, spec: str, *, unique: bool = False) -> None:
    """``spec`` is everything after ``ON <table>``, e.g. ``USING btree (a, b) WHERE ...``."""
    kind = "UNIQUE INDEX" if unique else "INDEX"
    _exec(f"CREATE {kind} IF NOT EXISTS {name} ON {table} {spec}")


def drop_index(name: str) -> None:
    _exec(f"DROP INDEX IF EXISTS {name}")


def execute_data(sql: str) -> None:
    """Data step coupled to a schema change. The SQL itself must be re-runnable."""
    _exec(sql)
