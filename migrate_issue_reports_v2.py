"""Add issue report context, category, notify_tags, and resolve columns.

Usage:
    DATABASE_URL=... python migrate_issue_reports_v2.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

COLUMNS = [
    ("category", "VARCHAR(32)"),
    ("notify_tags", "VARCHAR(32)[] NOT NULL DEFAULT '{}'"),
    ("reporter_telegram_user_id", "BIGINT"),
    ("club_id", "INTEGER REFERENCES clubs(id) ON DELETE SET NULL"),
    ("group_title", "VARCHAR(512)"),
    ("telegram_chat_id", "BIGINT"),
    ("resolved_at", "TIMESTAMPTZ"),
    ("resolved_by_telegram_user_id", "BIGINT"),
]

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_issue_reports_status
    ON issue_reports (status);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_issue_reports_club_id
    ON issue_reports (club_id);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for name, col_type in COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE issue_reports ADD COLUMN IF NOT EXISTS {name} {col_type}"
                )
            )
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("issue_reports v2 columns are ready.")
