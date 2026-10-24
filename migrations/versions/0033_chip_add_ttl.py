"""Remember a chip-add until it is sent or its ttl elapses.

Revision ID: 0033_chip_add_ttl
Revises: 0032_unbound_remind
Create Date: 2026-10-23
"""

from migrations import helpers as h

revision = "0033_chip_add_ttl"
down_revision = "0032_unbound_remind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "clubgg_rpa_events",
        "expires_at",
        "TIMESTAMP WITH TIME ZONE",
    )
    h.create_index(
        "ix_clubgg_rpa_expires",
        "clubgg_rpa_events",
        "(status, expires_at)",
    )


def downgrade() -> None:
    raise NotImplementedError
