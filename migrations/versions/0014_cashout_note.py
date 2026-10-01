"""Optional staff note on a cashout record.

Revision ID: 0014_cashout_note
Revises: 0013_audit_shifts
Create Date: 2026-10-01
"""

from migrations import helpers as h

revision = "0014_cashout_note"
down_revision = "0013_audit_shifts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("staff_cashout_records", "note", "TEXT")


def downgrade() -> None:
    raise NotImplementedError
