"""Add resolution notes, reminder tracking, and attachment type.

Usage:
    DATABASE_URL=... python migrate_issue_reports_resolve.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

ISSUE_REPORT_COLUMNS = [
    ("resolution_notes", "TEXT"),
    ("last_slack_reminder_at", "TIMESTAMPTZ"),
]

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_issue_reports_last_slack_reminder_at
    ON issue_reports (last_slack_reminder_at);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for name, col_type in ISSUE_REPORT_COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE issue_reports ADD COLUMN IF NOT EXISTS {name} {col_type}"
                )
            )
        conn.execute(
            text(
                "ALTER TABLE issue_report_attachments "
                "ADD COLUMN IF NOT EXISTS attachment_type VARCHAR(32) NOT NULL DEFAULT 'evidence'"
            )
        )
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("issue_reports resolve/reminder columns are ready.")
