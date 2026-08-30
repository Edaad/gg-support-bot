"""Allow variant weight 0 (inactive) on club_payment_tier_variants.

Usage:
    DATABASE_URL=... python migrate_variant_weight_zero.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    "ALTER TABLE club_payment_tier_variants "
    "DROP CONSTRAINT IF EXISTS ck_cptv_weight;",
    "ALTER TABLE club_payment_tier_variants "
    "ADD CONSTRAINT ck_cptv_weight CHECK (weight >= 0);",
]


def main() -> None:
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
    print("club_payment_tier_variants weight constraint updated (weight >= 0).")


if __name__ == "__main__":
    main()
