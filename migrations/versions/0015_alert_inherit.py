"""Link a deposit alert's disable cap to its Slack conditions.

Revision ID: 0015_alert_inherit
Revises: 0014_cashout_note
Create Date: 2026-10-01
"""

from migrations import helpers as h

revision = "0015_alert_inherit"
down_revision = "0014_cashout_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "deposit_method_alerts",
        "disable_inherits",
        "BOOLEAN NOT NULL DEFAULT false",
    )


def downgrade() -> None:
    raise NotImplementedError
