"""Track the $100 referral deposit bonus on each attribution.

Records when the referred group's bound payments met $100, whether the
referrer was told, whether Slack was posted or skipped, and whether an
existing qualifier was closed out without a message.

Revision ID: 0024_ref_deposit
Revises: 0023_clubgg_rpa
Create Date: 2026-10-05
"""

from migrations import helpers as h

revision = "0024_ref_deposit"
down_revision = "0023_clubgg_rpa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("referral_attributions", "deposit_met_at", "TIMESTAMPTZ")
    h.add_column("referral_attributions", "deposit_telegram_sent_at", "TIMESTAMPTZ")
    h.add_column("referral_attributions", "deposit_slack_sent_at", "TIMESTAMPTZ")
    h.add_column("referral_attributions", "deposit_slack_skipped_at", "TIMESTAMPTZ")
    h.add_column(
        "referral_attributions",
        "deposit_notify_suppressed",
        "BOOLEAN NOT NULL DEFAULT false",
    )


def downgrade() -> None:
    raise NotImplementedError
