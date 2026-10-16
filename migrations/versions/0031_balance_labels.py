"""PayPal and crypto balance cards can store a custom label.

Revision ID: 0031_balance_labels
Revises: 0030_contact_restore
Create Date: 2026-10-16
"""

from migrations import helpers as h

revision = "0031_balance_labels"
down_revision = "0030_contact_restore"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("account_balance_settings", "label", "VARCHAR(100)")
    h.drop_constraint("account_balance_settings", "ck_account_balance_settings_method")
    h.add_constraint(
        "account_balance_settings",
        "ck_account_balance_settings_method",
        "CHECK (method IN ('venmo', 'cashapp', 'paypal', 'crypto'))",
    )


def downgrade() -> None:
    raise NotImplementedError
