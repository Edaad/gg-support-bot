"""Allow negative expense amounts; enforce non-zero at DB level.

Negative amounts represent credits/refunds in the expense ledger.
Zero amounts remain invalid (matches API validation).

Usage:
    DATABASE_URL=... python migrate_expenses_allow_negative_amounts.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

CONSTRAINT = "expenses_amount_nonzero"

DDL = f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = '{CONSTRAINT}'
    ) THEN
        ALTER TABLE expenses
            ADD CONSTRAINT {CONSTRAINT} CHECK (amount <> 0);
    END IF;
END $$;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print(f"expenses table constraint {CONSTRAINT!r} is ready.")
