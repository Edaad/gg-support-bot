"""Delete pre-per-group cooldown bypasses and require chat_id.

Rows with NULL chat_id predate per-group bypasses; every lookup filters on chat_id,
so they can never match.

Revision ID: 0003_bypass_chat_id_not_null
Revises: 0002_missing_indexes
Create Date: 2026-09-23
"""

from migrations import helpers as h

revision = "0003_bypass_chat_id_not_null"
down_revision = "0002_missing_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.execute_data("DELETE FROM cooldown_bypasses WHERE chat_id IS NULL")
    h.set_not_null("cooldown_bypasses", "chat_id")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: add a new revision instead.")
