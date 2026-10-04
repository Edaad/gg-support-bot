"""Remember the last unread-group Pushover so restarts keep the 5-minute gap.

Revision ID: 0017_unread_alert
Revises: 0016_esc_slack_del
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0017_unread_alert"
down_revision = "0016_esc_slack_del"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "unread_group_alert_control",
        "id INTEGER PRIMARY KEY,\n"
        "    last_sent_at TIMESTAMP WITH TIME ZONE,\n"
        "    CONSTRAINT unread_group_alert_control_id_check CHECK (id = 1)",
    )
    h.execute_data(
        "INSERT INTO unread_group_alert_control (id) VALUES (1) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    raise NotImplementedError
