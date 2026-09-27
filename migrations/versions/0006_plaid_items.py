"""Plaid bank logins per club.

Revision ID: 0006_plaid_items
Revises: 0005_outbound_sends
Create Date: 2026-09-26
"""

from migrations import helpers as h

revision = "0006_plaid_items"
down_revision = "0005_outbound_sends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "plaid_items",
        """
        id SERIAL PRIMARY KEY,
        club_id INTEGER NOT NULL,
        item_id VARCHAR(128) NOT NULL,
        institution_id VARCHAR(64),
        institution_name VARCHAR(255) NOT NULL,
        access_token_encrypted TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "plaid_items",
        "plaid_items_club_id_fkey",
        "FOREIGN KEY (club_id) REFERENCES clubs(id) ON DELETE RESTRICT",
    )
    h.add_constraint(
        "plaid_items",
        "plaid_items_item_id_key",
        "UNIQUE (item_id)",
    )
    h.create_index(
        "ix_plaid_items_club_id",
        "plaid_items",
        "(club_id)",
    )


def downgrade() -> None:
    raise NotImplementedError
