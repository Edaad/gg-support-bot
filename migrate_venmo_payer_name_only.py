"""Venmo repeat-payer bindings: unique by payer name only (shared Venmo rotation).

Clubs use the same Venmo accounts in rotation; once linked, a payer is recognized
on any recipient @handle.

Usage:
    DATABASE_URL=... python migrate_venmo_payer_name_only.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

DEDUPE = """
DELETE FROM venmo_payer_bindings
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY payer_name_normalized
                   ORDER BY last_bound_at DESC NULLS LAST, id DESC
               ) AS rn
        FROM venmo_payer_bindings
    ) ranked
    WHERE rn > 1
);
"""

DROP_OLD_UQ = """
ALTER TABLE venmo_payer_bindings
    DROP CONSTRAINT IF EXISTS uq_venmo_payer_bindings_payer_handle;
"""

CREATE_NAME_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_venmo_payer_bindings_payer_name
    ON venmo_payer_bindings (payer_name_normalized);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DEDUPE))
        conn.execute(text(DROP_OLD_UQ))
        conn.execute(text(CREATE_NAME_UQ))
    print("venmo_payer_name_only migration complete.")


if __name__ == "__main__":
    main()
