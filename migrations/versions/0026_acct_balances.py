"""Per-tag offset and limits for Round Table balance cards.

Revision ID: 0026_acct_balances
Revises: 0025_zelle_night
Create Date: 2026-10-11
"""

from migrations import helpers as h

revision = "0026_acct_balances"
down_revision = "0025_zelle_night"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "account_balance_settings",
        """
        id SERIAL PRIMARY KEY,
        method VARCHAR(16) NOT NULL,
        tag VARCHAR(64) NOT NULL,
        offset_cents INTEGER NOT NULL,
        sending_ceiling_cents INTEGER NOT NULL,
        inflow_max_cents INTEGER NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "account_balance_settings",
        "uq_account_balance_settings_method_tag",
        "UNIQUE (method, tag)",
    )
    h.add_constraint(
        "account_balance_settings",
        "ck_account_balance_settings_method",
        "CHECK (method IN ('venmo', 'cashapp'))",
    )
    h.add_constraint(
        "account_balance_settings",
        "ck_account_balance_settings_sending_ceiling",
        "CHECK (sending_ceiling_cents > 0)",
    )
    h.add_constraint(
        "account_balance_settings",
        "ck_account_balance_settings_inflow_max",
        "CHECK (inflow_max_cents > 0)",
    )


def downgrade() -> None:
    raise NotImplementedError
