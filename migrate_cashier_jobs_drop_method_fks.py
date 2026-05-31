"""Drop legacy payment_method FKs on cashier_cashout_jobs (v2 method IDs).

After BOT_USE_PAYMENT_V2, method/sub-option IDs refer to club_payment_* tables,
not payment_methods / payment_sub_options. Storing v2 IDs hit FK violations.

Usage:
    DATABASE_URL=... python migrate_cashier_jobs_drop_method_fks.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import init_engine

DROPS = (
    "ALTER TABLE cashier_cashout_jobs "
    "DROP CONSTRAINT IF EXISTS cashier_cashout_jobs_payment_method_id_fkey",
    "ALTER TABLE cashier_cashout_jobs "
    "DROP CONSTRAINT IF EXISTS cashier_cashout_jobs_payment_sub_option_id_fkey",
)


def main() -> None:
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in DROPS:
            conn.execute(text(stmt))
        conn.commit()
    print("cashier_cashout_jobs method FK constraints dropped (if present).")


if __name__ == "__main__":
    main()
