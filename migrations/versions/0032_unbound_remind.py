"""Reminders for payments that arrive unbound.

Revision ID: 0032_unbound_remind
Revises: 0031_balance_labels
Create Date: 2026-10-22
"""

from migrations import helpers as h

revision = "0032_unbound_remind"
down_revision = "0031_balance_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "unbound_payment_reminders",
        """
        id SERIAL PRIMARY KEY,
        payment_method_slug VARCHAR(32) NOT NULL,
        payment_id INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        last_reminded_at TIMESTAMPTZ NULL
        """,
    )
    h.create_index(
        "uq_unbound_reminders_pay",
        "unbound_payment_reminders",
        "(payment_method_slug, payment_id)",
        unique=True,
    )
    h.create_index(
        "ix_unbound_reminders_due",
        "unbound_payment_reminders",
        "(created_at, last_reminded_at)",
    )


def downgrade() -> None:
    raise NotImplementedError
