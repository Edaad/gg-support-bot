"""Add PaymentMethod hyperlink settings for Stripe checkout placeholders.

Adds:
- payment_methods.use_group_checkout_link (bool, default false)
- payment_methods.hyperlink_text (varchar(64), nullable)
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
        if not _has_column(session, "payment_methods", "use_group_checkout_link"):
            session.execute(
                text(
                    """
                    ALTER TABLE payment_methods
                    ADD COLUMN use_group_checkout_link BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
            )

        if not _has_column(session, "payment_methods", "hyperlink_text"):
            session.execute(
                text(
                    """
                    ALTER TABLE payment_methods
                    ADD COLUMN hyperlink_text VARCHAR(64) NULL
                    """
                )
            )


if __name__ == "__main__":
    main()

