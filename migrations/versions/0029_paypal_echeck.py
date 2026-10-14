"""PayPal eCheck flag. True payments are refunded and not auto-added.

Revision ID: 0029_paypal_echeck
Revises: 0028_monday_review
Create Date: 2026-10-14
"""

from migrations import helpers as h

revision = "0029_paypal_echeck"
down_revision = "0028_monday_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "paypal_payments",
        "echeck",
        "BOOLEAN NOT NULL DEFAULT false",
    )


def downgrade() -> None:
    raise NotImplementedError
