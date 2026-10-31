"""Mark existing support groups bonus claimed, and remember duplicate notices.

Revision ID: 0035_fd_bonus_backfill
Revises: 0034_zelle_bank
Create Date: 2026-10-30
"""

from migrations import helpers as h

revision = "0035_fd_bonus_backfill"
down_revision = "0034_zelle_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "first_deposit_claim_backfill",
        """
        id INTEGER PRIMARY KEY
        """,
    )
    h.execute_data(
        """
        UPDATE groups
        SET first_deposit_claimed = true
        WHERE COALESCE(first_deposit_claimed, false) = false
          AND NOT EXISTS (
            SELECT 1 FROM first_deposit_claim_backfill WHERE id = 1
          )
        """
    )
    h.execute_data(
        """
        INSERT INTO first_deposit_claim_backfill (id)
        SELECT 1
        WHERE NOT EXISTS (
          SELECT 1 FROM first_deposit_claim_backfill WHERE id = 1
        )
        """
    )
    h.create_table(
        "first_deposit_duplicate_notices",
        """
        id SERIAL PRIMARY KEY,
        club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
        gg_player_id VARCHAR(255) NOT NULL,
        chat_key VARCHAR(512) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.create_index(
        "uq_fd_dup_notice",
        "first_deposit_duplicate_notices",
        "(club_id, gg_player_id, chat_key)",
        unique=True,
    )


def downgrade() -> None:
    raise NotImplementedError
