"""Remember a head-admin override of the GTO Zelle night window.

Revision ID: 0025_zelle_night
Revises: 0024_ref_deposit
Create Date: 2026-10-06
"""

from migrations import helpers as h

revision = "0025_zelle_night"
down_revision = "0024_ref_deposit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("club_payment_tier_variants", "night_release_on", "DATE")


def downgrade() -> None:
    raise NotImplementedError
