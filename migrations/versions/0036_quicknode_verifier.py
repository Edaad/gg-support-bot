"""QuickNode deposit verifier: webhook tx hints and verified transfers.

Revision ID: 0036_quicknode_verifier
Revises: 0035_fd_bonus_backfill
Create Date: 2026-11-25
"""

from migrations import helpers as h

revision = "0036_quicknode_verifier"
down_revision = "0035_fd_bonus_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "quicknode_tx_hints",
        """
        id BIGSERIAL PRIMARY KEY,
        chain VARCHAR(16) NOT NULL,
        tx_hash VARCHAR(128) NOT NULL,
        state VARCHAR(16) NOT NULL DEFAULT 'pending',
        reason TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        next_check_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        claimed_at TIMESTAMPTZ,
        alerted_at TIMESTAMPTZ
        """,
    )
    h.create_index(
        "uq_quicknode_tx_hints_chain_tx",
        "quicknode_tx_hints",
        "(chain, tx_hash)",
        unique=True,
    )
    h.create_index(
        "ix_quicknode_tx_hints_due",
        "quicknode_tx_hints",
        "(state, next_check_at)",
    )
    h.create_table(
        "quicknode_transfers",
        """
        id BIGSERIAL PRIMARY KEY,
        source_external_id VARCHAR(255) NOT NULL UNIQUE,
        chain VARCHAR(16) NOT NULL,
        tx_hash VARCHAR(128) NOT NULL,
        owner VARCHAR(8) NOT NULL,
        asset_symbol VARCHAR(16) NOT NULL,
        contract VARCHAR(128),
        to_address VARCHAR(128) NOT NULL,
        from_address VARCHAR(128),
        raw_amount TEXT NOT NULL,
        decimals INTEGER NOT NULL,
        usd_price TEXT,
        price_sources TEXT,
        amount_usd TEXT,
        state VARCHAR(16) NOT NULL,
        reason TEXT,
        explorer_url TEXT,
        block_number BIGINT,
        block_hash VARCHAR(128),
        crypto_payment_id INTEGER REFERENCES crypto_payments(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.create_index(
        "ix_quicknode_transfers_chain_tx",
        "quicknode_transfers",
        "(chain, tx_hash)",
    )
    h.create_index(
        "ix_quicknode_transfers_state",
        "quicknode_transfers",
        "(state)",
    )


def downgrade() -> None:
    raise NotImplementedError
