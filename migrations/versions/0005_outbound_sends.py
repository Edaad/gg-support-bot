"""Outbound Venmo and Cash App sends ingested from Zapier.

Revision ID: 0005_outbound_sends
Revises: 0004_drop_legacy_tables
Create Date: 2026-09-24
"""

from migrations import helpers as h

revision = "0005_outbound_sends"
down_revision = "0004_drop_legacy_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "outbound_sends",
        """
        id SERIAL PRIMARY KEY,
        method VARCHAR(16) NOT NULL,
        tag VARCHAR(64) NOT NULL,
        tag_matched BOOLEAN NOT NULL DEFAULT false,
        method_owner VARCHAR(32) NOT NULL,
        recipient VARCHAR(255) NOT NULL,
        amount_cents INTEGER NOT NULL,
        source_external_id VARCHAR(255) NOT NULL,
        paid_at VARCHAR(255),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "outbound_sends",
        "ck_outbound_sends_method",
        "CHECK (method IN ('venmo', 'cashapp'))",
    )
    h.add_constraint(
        "outbound_sends",
        "ck_outbound_sends_method_owner",
        "CHECK (method_owner IN ('round-table', 'vaughn', 'mateos'))",
    )
    h.add_constraint(
        "outbound_sends",
        "ck_outbound_sends_amount_cents",
        "CHECK (amount_cents > 0)",
    )
    h.create_index(
        "uq_outbound_sends_source_external_id",
        "outbound_sends",
        "(source_external_id)",
        unique=True,
    )
    h.create_index(
        "ix_outbound_sends_created_at",
        "outbound_sends",
        "(created_at)",
    )
    h.create_index(
        "ix_outbound_sends_method_created_at",
        "outbound_sends",
        "(method, created_at)",
    )
    h.create_index(
        "ix_outbound_sends_tag_created_at",
        "outbound_sends",
        "(tag, created_at)",
    )


def downgrade() -> None:
    raise NotImplementedError
