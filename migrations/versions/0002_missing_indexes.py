"""Create indexes the legacy migrate scripts defined but prod never received.

Revision ID: 0002_missing_indexes
Revises: 0001_baseline
Create Date: 2026-09-23
"""

from migrations import helpers as h

revision = "0002_missing_indexes"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_index(
        "ix_player_activities_club_chat_type",
        "player_activities",
        "USING btree (club_id, chat_id, activity_type, cancelled, created_at DESC)",
    )
    h.create_index(
        "ix_bonus_records_created_at", "bonus_records", "USING btree (created_at)"
    )
    h.create_index("ix_bonus_records_club_id", "bonus_records", "USING btree (club_id)")
    h.create_index(
        "ix_issue_report_attachments_report_id",
        "issue_report_attachments",
        "USING btree (issue_report_id)",
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only: add a new revision instead.")
