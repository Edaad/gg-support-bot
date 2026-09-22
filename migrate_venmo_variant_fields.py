"""Add Venmo destination fields on club_payment_tier_variants and backfill.

Adds:
- venmo_tag (varchar 32)
- venmo_link (varchar 128)
- venmo_response_mode (varchar 16: default | text | photo)

Backfills Venmo method variants only: when response_text/caption contain exactly
one unique https://venmo.com/u/{user} link, writes link + @tag. Sets mode from
response_type (photo stays photo, otherwise text). Never switches to default.

Usage:
    DATABASE_URL=... python migrate_venmo_variant_fields.py
"""

from __future__ import annotations

from sqlalchemy import text

from bot.services.venmo_variant_fields import (
    extract_unique_venmo_link_from_text,
    normalize_venmo_tag,
    username_from_venmo_link,
)
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


def _ensure_columns(session) -> None:
    table = "club_payment_tier_variants"
    if not _has_column(session, table, "venmo_tag"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN venmo_tag VARCHAR(32) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.venmo_tag")
    else:
        print("club_payment_tier_variants.venmo_tag already present")

    if not _has_column(session, table, "venmo_link"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN venmo_link VARCHAR(128) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.venmo_link")
    else:
        print("club_payment_tier_variants.venmo_link already present")

    if not _has_column(session, table, "venmo_response_mode"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN venmo_response_mode VARCHAR(16) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.venmo_response_mode")
    else:
        print("club_payment_tier_variants.venmo_response_mode already present")


def _backfill(session) -> None:
    rows = session.execute(
        text(
            """
            SELECT v.id, v.response_type, v.response_text, v.response_caption,
                   v.venmo_tag, v.venmo_link, v.venmo_response_mode
            FROM club_payment_tier_variants v
            JOIN club_payment_methods m ON m.id = v.method_id
            WHERE m.slug = 'venmo'
            """
        )
    ).fetchall()

    filled_link = 0
    filled_mode = 0
    skipped_link = 0
    for row in rows:
        (
            variant_id,
            response_type,
            response_text,
            response_caption,
            venmo_tag,
            venmo_link,
            venmo_response_mode,
        ) = row

        updates: dict[str, str] = {}

        if not venmo_response_mode:
            mode = (
                "photo" if (response_type or "").strip().lower() == "photo" else "text"
            )
            updates["venmo_response_mode"] = mode
            filled_mode += 1

        if not venmo_link or not venmo_tag:
            link = venmo_link or extract_unique_venmo_link_from_text(
                response_text, response_caption
            )
            if link:
                tag = normalize_venmo_tag(username_from_venmo_link(link) or "")
                if not venmo_link:
                    updates["venmo_link"] = link
                if not venmo_tag and tag:
                    updates["venmo_tag"] = tag
                if "venmo_link" in updates or "venmo_tag" in updates:
                    filled_link += 1
            else:
                skipped_link += 1

        if not updates:
            continue
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        session.execute(
            text(f"UPDATE club_payment_tier_variants SET {sets} WHERE id = :id"),
            {**updates, "id": variant_id},
        )

    print(
        f"Backfill: mode set on {filled_mode} row(s); "
        f"link/tag filled on {filled_link} row(s); "
        f"link/tag skipped (0 or >1 links) on {skipped_link} row(s)."
    )


def main() -> None:
    with get_db() as session:
        _ensure_columns(session)
        _backfill(session)
    print("migrate_venmo_variant_fields: done.")


if __name__ == "__main__":
    main()
