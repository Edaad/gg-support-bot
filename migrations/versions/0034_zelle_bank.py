"""Remember the Zelle destination shown with deposit instructions.

Revision ID: 0034_zelle_bank
Revises: 0033_chip_add_ttl
Create Date: 2026-10-30
"""

from migrations import helpers as h

revision = "0034_zelle_bank"
down_revision = "0033_chip_add_ttl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "support_group_chats",
        "escalation_deposit_zelle_recipient",
        "VARCHAR(100)",
    )


def downgrade() -> None:
    raise NotImplementedError
