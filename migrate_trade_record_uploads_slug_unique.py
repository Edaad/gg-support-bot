"""One-time migration: trade_record_uploads unique on (club_slug, audit_date).

RT and Aces Table share the same Postgres club_id but are distinct gg-computer
slugs; the old (club_id, audit_date) constraint blocked same-day dual uploads.

Usage:
    DATABASE_URL=... python migrate_trade_record_uploads_slug_unique.py
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STEPS = [
    "ALTER TABLE trade_record_uploads DROP CONSTRAINT IF EXISTS uq_trade_record_uploads_club_date;",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_trade_record_uploads_slug_date'
        ) THEN
            ALTER TABLE trade_record_uploads
            ADD CONSTRAINT uq_trade_record_uploads_slug_date
            UNIQUE (club_slug, audit_date);
        END IF;
    END $$;
    """,
]

with engine.connect() as conn:
    for stmt in STEPS:
        conn.execute(text(stmt))
    conn.commit()
    print("trade_record_uploads: unique constraint is now (club_slug, audit_date).")
