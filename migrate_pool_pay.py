"""Add pool_pay_type and migrate union method slugs to structured format.

Usage:
    DATABASE_URL=... python migrate_pool_pay.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from sqlalchemy import text

from bot.services.pool_pay_types import build_pool_pay_slug, parse_pool_pay_slug
from bot.services.union_method_types import validate_union_method_type
from db.connection import init_engine

STMTS = [
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS pool_pay_type VARCHAR(20) NOT NULL DEFAULT 'union_method'
    """,
]


def _method_type_slug(union_type: str | None, name: str | None, slug: str | None) -> str | None:
    if union_type:
        try:
            return validate_union_method_type(str(union_type))
        except ValueError:
            pass
    parsed = parse_pool_pay_slug(slug or "")
    if parsed:
        return parsed[0]
    return None


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))

        rows = conn.execute(
            text(
                """
                SELECT id, slug, union_type, name
                FROM club_payment_methods
                WHERE tracks_manual_requests = true
                  AND direction = 'deposit'
                ORDER BY id
                """
            )
        ).fetchall()

        for row in rows:
            method_id = int(row[0])
            old_slug = (row[1] or "").strip().lower()
            type_slug = _method_type_slug(row[2], row[3], old_slug)
            if not type_slug:
                print(f"  skip id={method_id}: could not resolve union type for slug={old_slug!r}")
                continue

            parsed = parse_pool_pay_slug(old_slug)
            if parsed and parsed[0] == type_slug and parsed[1] == "union_method":
                new_slug = old_slug
            else:
                new_slug = build_pool_pay_slug(type_slug, "union_method", old_slug)

            if new_slug == old_slug:
                conn.execute(
                    text(
                        """
                        UPDATE club_payment_methods
                        SET pool_pay_type = 'union_method'
                        WHERE id = :id
                        """
                    ),
                    {"id": method_id},
                )
                continue

            conn.execute(
                text(
                    """
                    UPDATE club_payment_methods
                    SET slug = :new_slug, pool_pay_type = 'union_method'
                    WHERE id = :id
                    """
                ),
                {"id": method_id, "new_slug": new_slug},
            )
            conn.execute(
                text(
                    """
                    UPDATE manual_deposit_requests
                    SET method_slug = :new_slug
                    WHERE method_id = :id AND method_slug = :old_slug
                    """
                ),
                {"id": method_id, "new_slug": new_slug, "old_slug": old_slug},
            )
            print(f"  migrated id={method_id}: {old_slug!r} -> {new_slug!r}")

    print("migrate_pool_pay: done")


if __name__ == "__main__":
    main()
