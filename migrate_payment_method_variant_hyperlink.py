"""Add per-variant Stripe group-checkout settings (same fields as tiers).

Adds to method_variants:
- min_amount, max_amount
- use_group_checkout_link (bool, default false)
- group_checkout_provider (varchar(32), nullable)
- hyperlink_text (varchar(64), nullable)
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import get_db


def _has_column(session, table: str, column: str) -> bool:
    q = text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :table
          AND column_name = :column
        LIMIT 1
        """
    )
    return session.execute(q, {"table": table, "column": column}).first() is not None


def main() -> None:
    with get_db() as session:
        if not _has_column(session, "method_variants", "min_amount"):
            session.execute(
                text(
                    """
                    ALTER TABLE method_variants
                    ADD COLUMN min_amount NUMERIC(12, 2) NULL
                    """
                )
            )
        if not _has_column(session, "method_variants", "max_amount"):
            session.execute(
                text(
                    """
                    ALTER TABLE method_variants
                    ADD COLUMN max_amount NUMERIC(12, 2) NULL
                    """
                )
            )
        if not _has_column(session, "method_variants", "use_group_checkout_link"):
            session.execute(
                text(
                    """
                    ALTER TABLE method_variants
                    ADD COLUMN use_group_checkout_link BOOLEAN NULL
                    """
                )
            )
        if not _has_column(session, "method_variants", "group_checkout_provider"):
            session.execute(
                text(
                    """
                    ALTER TABLE method_variants
                    ADD COLUMN group_checkout_provider VARCHAR(32) NULL
                    """
                )
            )
        if not _has_column(session, "method_variants", "hyperlink_text"):
            session.execute(
                text(
                    """
                    ALTER TABLE method_variants
                    ADD COLUMN hyperlink_text VARCHAR(64) NULL
                    """
                )
            )


if __name__ == "__main__":
    main()
