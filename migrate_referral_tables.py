"""Create referral_links and referral_attributions tables.

Usage:
    DATABASE_URL=... python migrate_referral_tables.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_LINKS = """
CREATE TABLE IF NOT EXISTS referral_links (
    id SERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    referrer_gg_player_id VARCHAR(255) NOT NULL,
    referrer_chat_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_referral_links_club_chat UNIQUE (club_id, referrer_chat_id),
    CONSTRAINT uq_referral_links_club_player UNIQUE (club_id, referrer_gg_player_id)
);
"""

DDL_ATTRIBUTIONS = """
CREATE TABLE IF NOT EXISTS referral_attributions (
    id SERIAL PRIMARY KEY,
    referral_link_id INTEGER NOT NULL REFERENCES referral_links(id) ON DELETE CASCADE,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    clicker_telegram_user_id BIGINT NOT NULL,
    referred_gg_player_id VARCHAR(255),
    referred_chat_id BIGINT,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    credited_at TIMESTAMPTZ,
    acked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_referral_attr_club_clicker UNIQUE (club_id, clicker_telegram_user_id)
);
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_referral_links_code ON referral_links (code);",
    "CREATE INDEX IF NOT EXISTS ix_referral_links_club_id ON referral_links (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_referral_attr_link_id ON referral_attributions (referral_link_id);",
    "CREATE INDEX IF NOT EXISTS ix_referral_attr_club_id ON referral_attributions (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_referral_attr_status ON referral_attributions (status);",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_attr_club_referred_chat
    ON referral_attributions (club_id, referred_chat_id)
    WHERE referred_chat_id IS NOT NULL;
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_LINKS))
        conn.execute(text(DDL_ATTRIBUTIONS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("referral_links and referral_attributions are ready.")
