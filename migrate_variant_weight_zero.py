"""Allow variant weight 0 (inactive) on club_payment_tier_variants.

Removes legacy weight >= 1 checks (inline table DDL and SQLAlchemy-named
constraint), then ensures a single ck_cptv_weight CHECK (weight >= 0).

Usage:
    DATABASE_URL=... python migrate_variant_weight_zero.py
    heroku run -a gg-support-bot-2025 -- python migrate_variant_weight_zero.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import init_engine

# Postgres auto-names inline column checks {table}_{column}_check; SQLAlchemy
# model / later migrations may use ck_cptv_weight. Drop every known name, then
# recreate one canonical constraint.
DROP_WEIGHT_CONSTRAINTS = [
    "ALTER TABLE club_payment_tier_variants "
    "DROP CONSTRAINT IF EXISTS club_payment_tier_variants_weight_check;",
    "ALTER TABLE club_payment_tier_variants "
    "DROP CONSTRAINT IF EXISTS ck_cptv_weight;",
]

ADD_WEIGHT_CONSTRAINT = (
    "ALTER TABLE club_payment_tier_variants "
    "ADD CONSTRAINT ck_cptv_weight CHECK (weight >= 0);"
)

LIST_WEIGHT_CHECKS = text(
    """
    SELECT conname, pg_get_constraintdef(oid) AS def
    FROM pg_constraint
    WHERE conrelid = 'club_payment_tier_variants'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%weight%'
    ORDER BY conname
    """
)


def main() -> None:
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in DROP_WEIGHT_CONSTRAINTS:
            conn.execute(text(stmt))
        conn.execute(text(ADD_WEIGHT_CONSTRAINT))
        conn.commit()

        rows = conn.execute(LIST_WEIGHT_CHECKS).fetchall()
        if len(rows) == 1 and rows[0][0] == "ck_cptv_weight" and "weight >= 0" in rows[0][1]:
            print(
                "club_payment_tier_variants weight constraint OK: "
                "ck_cptv_weight CHECK (weight >= 0)."
            )
        else:
            print("club_payment_tier_variants weight constraints after migration:")
            for name, defn in rows:
                print(f"  {name}: {defn}")
            if not any(r[0] == "ck_cptv_weight" for r in rows):
                raise RuntimeError(
                    "Migration finished but ck_cptv_weight is missing; check DB manually."
                )
            if len(rows) != 1:
                raise RuntimeError(
                    "Expected exactly one weight check constraint (ck_cptv_weight); "
                    f"found {len(rows)}."
                )


if __name__ == "__main__":
    main()
