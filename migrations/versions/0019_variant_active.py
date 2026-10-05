"""Active flag for deposit variants, separate from rotation weight.

Weight 0 used to mean inactive. Those rows are marked inactive so they stay
out of rotation; weight itself is only the share among active variants.

Revision ID: 0019_variant_active
Revises: 0018_unread_backoff
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0019_variant_active"
down_revision = "0018_unread_backoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "club_payment_tier_variants",
        "is_active",
        "BOOLEAN NOT NULL DEFAULT true",
    )
    # Old sentinel: weight 0 was the off switch. Re-runnable while those rows
    # stay inactive; a later active variant may use any weight, including 0.
    h.execute_data(
        "UPDATE club_payment_tier_variants "
        "SET is_active = false "
        "WHERE weight = 0 AND is_active = true"
    )


def downgrade() -> None:
    raise NotImplementedError
