"""Money-send proof + player notify queue, and owed-pin clearing on cashout records.

Revision ID: 0010_money_send_notify
Revises: 0009_crypto_outbound
Create Date: 2026-09-28
"""

from migrations import helpers as h

revision = "0010_money_send_notify"
down_revision = "0009_crypto_outbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "staff_cashout_money_sends",
        "notify_player",
        "BOOLEAN NOT NULL DEFAULT false",
    )
    h.add_column("staff_cashout_money_sends", "notify_status", "VARCHAR(20)")
    h.add_column("staff_cashout_money_sends", "notify_error", "TEXT")
    h.add_column("staff_cashout_money_sends", "notified_at", "TIMESTAMP")
    h.add_column("staff_cashout_money_sends", "proof_link", "TEXT")
    h.add_column(
        "staff_cashout_money_sends",
        "has_proof",
        "BOOLEAN NOT NULL DEFAULT false",
    )
    h.create_index(
        "ix_staff_cashout_money_sends_notify_status",
        "staff_cashout_money_sends",
        "(notify_status)",
    )

    h.create_table(
        "staff_cashout_send_proofs",
        """
        id SERIAL PRIMARY KEY,
        money_send_id INTEGER NOT NULL,
        filename VARCHAR(255) NOT NULL,
        content_type VARCHAR(128) NOT NULL,
        content BYTEA NOT NULL,
        created_at TIMESTAMP DEFAULT now()
        """,
    )
    h.add_constraint(
        "staff_cashout_send_proofs",
        "staff_cashout_send_proofs_money_send_id_fkey",
        "FOREIGN KEY (money_send_id) REFERENCES staff_cashout_money_sends(id) "
        "ON DELETE CASCADE",
    )
    h.add_constraint(
        "staff_cashout_send_proofs",
        "staff_cashout_send_proofs_money_send_id_key",
        "UNIQUE (money_send_id)",
    )

    h.add_column("staff_cashout_records", "owed_message_id", "BIGINT")
    h.add_column("staff_cashout_records", "owed_clear_status", "VARCHAR(20)")
    h.add_column("staff_cashout_records", "owed_clear_error", "TEXT")
    h.add_column("staff_cashout_records", "owed_cleared_at", "TIMESTAMP")


def downgrade() -> None:
    raise NotImplementedError
