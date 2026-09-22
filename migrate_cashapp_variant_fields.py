"""Add Cash App destination fields on club_payment_tier_variants and backfill.

Adds:
- cashapp_tag (varchar 32)
- cashapp_link (varchar 128)
- cashapp_response_mode (varchar 16: default | text | photo)

Backfills native (non-checkout) Cash App method variants only: when
response_text/caption contain exactly one unique cash.app cashtag, writes
link + $tag. Sets mode from response_type (photo stays photo, otherwise text).
Never switches to default. Checkout variants (variant or tier Stripe link) are
skipped for link/tag backfill and still get mode when empty.

Usage:
    DATABASE_URL=... python migrate_cashapp_variant_fields.py
"""

from __future__ import annotations

from sqlalchemy import text

from bot.services.cashapp_variant_fields import (
    cashtag_from_link,
    extract_unique_cashapp_link_from_text,
    normalize_cashapp_tag,
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
    if not _has_column(session, table, "cashapp_tag"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN cashapp_tag VARCHAR(32) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.cashapp_tag")
    else:
        print("club_payment_tier_variants.cashapp_tag already present")

    if not _has_column(session, table, "cashapp_link"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN cashapp_link VARCHAR(128) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.cashapp_link")
    else:
        print("club_payment_tier_variants.cashapp_link already present")

    if not _has_column(session, table, "cashapp_response_mode"):
        session.execute(
            text(
                """
                ALTER TABLE club_payment_tier_variants
                ADD COLUMN cashapp_response_mode VARCHAR(16) NULL
                """
            )
        )
        print("Added club_payment_tier_variants.cashapp_response_mode")
    else:
        print("club_payment_tier_variants.cashapp_response_mode already present")


def _is_checkout(variant_link, tier_link) -> bool:
    if variant_link is True:
        return True
    if variant_link is False:
        return False
    return bool(tier_link)


def _backfill(session) -> None:
    rows = session.execute(
        text(
            """
            SELECT v.id, v.response_type, v.response_text, v.response_caption,
                   v.use_group_checkout_link, t.use_group_checkout_link AS tier_checkout,
                   v.cashapp_tag, v.cashapp_link, v.cashapp_response_mode
            FROM club_payment_tier_variants v
            JOIN club_payment_methods m ON m.id = v.method_id
            JOIN club_payment_tiers t ON t.id = v.tier_id
            WHERE m.slug = 'cashapp'
            """
        )
    ).fetchall()

    filled_link = 0
    filled_mode = 0
    skipped_link = 0
    skipped_checkout = 0
    for row in rows:
        (
            variant_id,
            response_type,
            response_text,
            response_caption,
            variant_checkout,
            tier_checkout,
            cashapp_tag,
            cashapp_link,
            cashapp_response_mode,
        ) = row

        updates: dict[str, str] = {}
        is_checkout = _is_checkout(variant_checkout, tier_checkout)

        if not cashapp_response_mode:
            mode = (
                "photo" if (response_type or "").strip().lower() == "photo" else "text"
            )
            updates["cashapp_response_mode"] = mode
            filled_mode += 1

        if is_checkout:
            skipped_checkout += 1
        elif not cashapp_link or not cashapp_tag:
            link = cashapp_link or extract_unique_cashapp_link_from_text(
                response_text, response_caption
            )
            if link:
                tag = normalize_cashapp_tag(cashtag_from_link(link) or "")
                if not cashapp_link:
                    updates["cashapp_link"] = link
                if not cashapp_tag and tag:
                    updates["cashapp_tag"] = tag
                if "cashapp_link" in updates or "cashapp_tag" in updates:
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
        f"link/tag skipped (0 or >1) on {skipped_link} row(s); "
        f"checkout skipped for link/tag on {skipped_checkout} row(s)."
    )


def main() -> None:
    with get_db() as session:
        _ensure_columns(session)
        _backfill(session)
    print("migrate_cashapp_variant_fields: done.")


if __name__ == "__main__":
    main()
