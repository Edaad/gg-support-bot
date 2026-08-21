"""Create expenses table for the admin Expenses dashboard.

Usage:
    DATABASE_URL=... python migrate_expenses.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    amount NUMERIC(12, 2) NOT NULL,
    expense_type VARCHAR(255) NOT NULL,
    description TEXT,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE RESTRICT,
    expense_date DATE NOT NULL,
    pending BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_expenses_expense_date ON expenses (expense_date);",
    "CREATE INDEX IF NOT EXISTS ix_expenses_club_id ON expenses (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_expenses_pending ON expenses (pending);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("expenses table is ready.")
