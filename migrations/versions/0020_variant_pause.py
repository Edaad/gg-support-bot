"""Temporary pause window for a deposit variant.

Revision ID: 0020_variant_pause
Revises: 0019_variant_active
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0020_variant_pause"
down_revision = "0019_variant_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "club_payment_tier_variants",
        "paused_until",
        "TIMESTAMP WITH TIME ZONE",
    )


def downgrade() -> None:
    raise NotImplementedError
