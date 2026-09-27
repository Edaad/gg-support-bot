"""Plaid transaction sync and Zelle fields.

Revision ID: 0007_plaid_txns
Revises: 0006_plaid_items
Create Date: 2026-09-27
"""

from migrations import helpers as h

revision = "0007_plaid_txns"
down_revision = "0006_plaid_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("plaid_items", "transactions_cursor", "TEXT")
    h.add_column(
        "plaid_items",
        "sync_status",
        "VARCHAR(16) NOT NULL DEFAULT 'idle'",
    )
    h.add_column("plaid_items", "sync_started_at", "TIMESTAMPTZ")
    h.add_column("plaid_items", "last_synced_at", "TIMESTAMPTZ")
    h.add_column("plaid_items", "sync_error", "TEXT")
    h.create_table(
        "plaid_transactions",
        """
        id SERIAL PRIMARY KEY,
        plaid_item_id INTEGER NOT NULL,
        transaction_id VARCHAR(128) NOT NULL,
        account_id VARCHAR(128),
        amount NUMERIC(14, 2) NOT NULL,
        iso_currency_code VARCHAR(8),
        txn_date DATE NOT NULL,
        name TEXT,
        merchant_name TEXT,
        original_description TEXT,
        pending BOOLEAN NOT NULL DEFAULT false,
        payment_channel VARCHAR(32),
        payer TEXT,
        payee TEXT,
        memo TEXT,
        payment_method TEXT,
        is_zelle BOOLEAN NOT NULL DEFAULT false,
        detail_json TEXT
        """,
    )
    h.add_constraint(
        "plaid_transactions",
        "plaid_transactions_plaid_item_id_fkey",
        "FOREIGN KEY (plaid_item_id) REFERENCES plaid_items(id) ON DELETE RESTRICT",
    )
    h.add_constraint(
        "plaid_transactions",
        "plaid_transactions_transaction_id_key",
        "UNIQUE (transaction_id)",
    )
    h.create_index(
        "ix_plaid_transactions_item_date",
        "plaid_transactions",
        "(plaid_item_id, txn_date)",
    )
    h.create_index(
        "ix_plaid_transactions_zelle_date",
        "plaid_transactions",
        "(is_zelle, txn_date)",
    )


def downgrade() -> None:
    raise NotImplementedError
