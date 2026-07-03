"""Add club_slug and audit_timezone_policy to trade_record_uploads.

Usage:
    DATABASE_URL=... python migrate_trade_record_timezone.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from api.club_audit_timezone import SLUG_TO_POLICY, audit_timezone_for_slug
from api.trade_record_parser import (
    TradeRecordMetadata,
    extract_audit_date_from_metadata,
    resolve_club_slug_from_metadata,
)
from db.connection import init_engine

COLUMNS = [
    ("club_slug", "VARCHAR(64)"),
    ("audit_timezone_policy", "VARCHAR(32)"),
]

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_trade_record_uploads_club_slug
    ON trade_record_uploads (club_slug);
    """,
]


def _backfill_slug_from_metadata(metadata_json: str | None) -> str | None:
    if not metadata_json:
        return None
    try:
        data = json.loads(metadata_json)
    except json.JSONDecodeError:
        return None
    club_text = str(data.get("club_text") or "").strip()
    if not club_text:
        return None
    metadata = TradeRecordMetadata(
        club_text=club_text,
        club_id_text=str(data.get("club_id_text") or ""),
        date_text=str(data.get("date_text") or ""),
    )
    try:
        return resolve_club_slug_from_metadata(metadata)
    except Exception:
        return None


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for name, col_type in COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE trade_record_uploads "
                    f"ADD COLUMN IF NOT EXISTS {name} {col_type}"
                )
            )
        for stmt in INDEXES:
            conn.execute(text(stmt))

        rows = conn.execute(
            text(
                """
                SELECT id, metadata_json, club_slug, audit_timezone_policy
                FROM trade_record_uploads
                WHERE club_slug IS NULL OR audit_timezone_policy IS NULL
                """
            )
        ).fetchall()

        for row in rows:
            upload_id, metadata_json, club_slug, audit_timezone_policy = row
            slug = club_slug or _backfill_slug_from_metadata(metadata_json)
            if not slug:
                continue
            policy = audit_timezone_policy or audit_timezone_for_slug(slug).value
            conn.execute(
                text(
                    """
                    UPDATE trade_record_uploads
                    SET club_slug = :slug, audit_timezone_policy = :policy
                    WHERE id = :id
                    """
                ),
                {"slug": slug, "policy": policy, "id": upload_id},
            )

    print(
        "trade_record_uploads club_slug and audit_timezone_policy columns are ready "
        f"({len(SLUG_TO_POLICY)} slug policies configured)."
    )


if __name__ == "__main__":
    main()
