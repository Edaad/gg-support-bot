"""Create group_payment_method_bindings and payment_method_bind_attempts tables.

Usage:
    DATABASE_URL=... python migrate_payment_method_bindings.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_GROUP_BINDINGS = """
CREATE TABLE IF NOT EXISTS group_payment_method_bindings (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    payment_method_slug VARCHAR(32) NOT NULL,
    variant_id INTEGER REFERENCES club_payment_tier_variants(id) ON DELETE SET NULL,
    venmo_handle VARCHAR(100),
    bound_via VARCHAR(32) NOT NULL,
    bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bound_by_telegram_user_id BIGINT,
    first_bind_attempt_id INTEGER,
    CONSTRAINT uq_gpm_bindings_chat_method
        UNIQUE (telegram_chat_id, payment_method_slug)
);
"""

DDL_BIND_ATTEMPTS = """
CREATE TABLE IF NOT EXISTS payment_method_bind_attempts (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    payment_method_slug VARCHAR(32) NOT NULL,
    method_id INTEGER NOT NULL,
    tier_id INTEGER REFERENCES club_payment_tiers(id) ON DELETE SET NULL,
    variant_id INTEGER NOT NULL REFERENCES club_payment_tier_variants(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    bound_via VARCHAR(32) NOT NULL DEFAULT 'special_amount',
    initiated_by_telegram_user_id BIGINT,
    venmo_payment_id INTEGER REFERENCES venmo_payments(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_gpm_bindings_telegram_chat_id
    ON group_payment_method_bindings (telegram_chat_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_gpm_bindings_club_slug
    ON group_payment_method_bindings (club_id, payment_method_slug);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pmba_variant_status
    ON payment_method_bind_attempts (variant_id, status);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pmba_chat_method_status
    ON payment_method_bind_attempts (telegram_chat_id, payment_method_slug, status);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pmba_created_at
    ON payment_method_bind_attempts (created_at);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_pmba_pending_variant_amount
    ON payment_method_bind_attempts (variant_id, amount_cents)
    WHERE status = 'pending';
    """,
]

FK_ATTEMPT_FIRST_BIND = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_gpm_bindings_first_bind_attempt'
    ) THEN
        ALTER TABLE group_payment_method_bindings
        ADD CONSTRAINT fk_gpm_bindings_first_bind_attempt
        FOREIGN KEY (first_bind_attempt_id)
        REFERENCES payment_method_bind_attempts(id) ON DELETE SET NULL;
    END IF;
END $$;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_BIND_ATTEMPTS))
        conn.execute(text(DDL_GROUP_BINDINGS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.execute(text(FK_ATTEMPT_FIRST_BIND))
        conn.commit()
        print(
            "group_payment_method_bindings and payment_method_bind_attempts are ready."
        )
