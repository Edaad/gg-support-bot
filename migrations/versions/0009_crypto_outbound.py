"""Allow crypto rows on outbound sends.

Revision ID: 0009_crypto_outbound
Revises: 0008_plaid_seen
Create Date: 2026-09-28
"""

from migrations import helpers as h

revision = "0009_crypto_outbound"
down_revision = "0008_plaid_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.drop_constraint("outbound_sends", "ck_outbound_sends_method")
    h.add_constraint(
        "outbound_sends",
        "ck_outbound_sends_method",
        "CHECK (method IN ('venmo', 'cashapp', 'crypto'))",
    )


def downgrade() -> None:
    raise NotImplementedError
