"""Remember staff-unanswered Slack posts so they can be deleted later.

Revision ID: 0016_esc_slack_del
Revises: 0015_alert_inherit
Create Date: 2026-10-03
"""

from migrations import helpers as h

revision = "0016_esc_slack_del"
down_revision = "0015_alert_inherit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("escalation_events", "slack_channel_id", "VARCHAR(64)")
    h.add_column("escalation_events", "slack_message_ts", "VARCHAR(64)")
    h.add_column("escalation_events", "slack_delete_at", "TIMESTAMPTZ")
    h.create_index(
        "ix_esc_ev_slack_delete_at",
        "escalation_events",
        "USING btree (slack_delete_at) WHERE slack_delete_at IS NOT NULL",
    )


def downgrade() -> None:
    raise NotImplementedError
