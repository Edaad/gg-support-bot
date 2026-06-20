"""Create issue_reports and issue_report_attachments tables.

Usage:
    DATABASE_URL=... python migrate_issue_reports.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_ISSUE_REPORTS = """
CREATE TABLE IF NOT EXISTS issue_reports (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    tags VARCHAR(32)[] NOT NULL DEFAULT '{}',
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    reporter_name VARCHAR(255),
    reporter_source VARCHAR(32) NOT NULL DEFAULT 'api',
    slack_message_ts VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_ATTACHMENTS = """
CREATE TABLE IF NOT EXISTS issue_report_attachments (
    id SERIAL PRIMARY KEY,
    issue_report_id INTEGER NOT NULL REFERENCES issue_reports(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    content BYTEA NOT NULL,
    slack_file_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_issue_reports_created_at
    ON issue_reports (created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_issue_report_attachments_report_id
    ON issue_report_attachments (issue_report_id);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_ISSUE_REPORTS))
        conn.execute(text(DDL_ATTACHMENTS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("issue_reports and issue_report_attachments are ready.")
