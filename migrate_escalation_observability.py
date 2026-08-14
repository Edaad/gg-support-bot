"""Create escalation_episodes / escalation_events and live history_episode_id.

Usage:
    DATABASE_URL=... python migrate_escalation_observability.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (uuid / timestamptz / jsonb).
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from sqlalchemy import text

from db.connection import init_engine

DDL_EPISODES = """
CREATE TABLE IF NOT EXISTS escalation_episodes (
    id UUID PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    group_title TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    close_reason VARCHAR(32),
    trigger_messages JSONB NOT NULL DEFAULT '[]'::jsonb
);
"""

DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS escalation_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason VARCHAR(64) NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    telegram_chat_id BIGINT NOT NULL,
    group_title TEXT,
    episode_id UUID REFERENCES escalation_episodes(id) ON DELETE SET NULL,
    slack_ok BOOLEAN NOT NULL DEFAULT FALSE,
    head_admin_fanout BOOLEAN NOT NULL DEFAULT FALSE,
    method_slug VARCHAR(64),
    trigger_messages JSONB NOT NULL DEFAULT '[]'::jsonb
);
"""

DDL_LIVE_COL = """
ALTER TABLE support_group_idle_episode_state
    ADD COLUMN IF NOT EXISTS history_episode_id UUID
    REFERENCES escalation_episodes(id) ON DELETE SET NULL;
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_esc_ep_chat_opened_at
    ON escalation_episodes (telegram_chat_id, opened_at);
CREATE INDEX IF NOT EXISTS ix_esc_ev_created_at
    ON escalation_events (created_at);
CREATE INDEX IF NOT EXISTS ix_esc_ev_chat_created_at
    ON escalation_events (telegram_chat_id, created_at);
CREATE INDEX IF NOT EXISTS ix_esc_ev_episode_id
    ON escalation_events (episode_id);
CREATE INDEX IF NOT EXISTS ix_esc_ev_reason_created_at
    ON escalation_events (reason, created_at);
"""


def ensure_escalation_observability(engine=None) -> None:
    if engine is None:
        engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL_EPISODES))
        conn.execute(text(DDL_EVENTS))
        conn.execute(text(DDL_LIVE_COL))
        conn.execute(text(INDEXES))


if __name__ == "__main__":
    ensure_escalation_observability()
    print("escalation_episodes / escalation_events tables are ready.")
