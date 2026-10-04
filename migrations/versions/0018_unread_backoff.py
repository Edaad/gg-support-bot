"""Store the unread-alert backoff step so a restart keeps the current gap.

Revision ID: 0018_unread_backoff
Revises: 0017_unread_alert
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0018_unread_backoff"
down_revision = "0017_unread_alert"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "unread_group_alert_control",
        "backoff_step",
        "INTEGER NOT NULL DEFAULT 0",
    )


def downgrade() -> None:
    raise NotImplementedError
