"""When a Plaid transaction was first seen after the history pull.

Revision ID: 0008_plaid_seen
Revises: 0007_plaid_txns
Create Date: 2026-09-27
"""

from migrations import helpers as h

revision = "0008_plaid_seen"
down_revision = "0007_plaid_txns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("plaid_transactions", "first_seen_at", "TIMESTAMPTZ")


def downgrade() -> None:
    raise NotImplementedError
