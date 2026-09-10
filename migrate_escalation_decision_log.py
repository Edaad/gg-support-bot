"""Create escalation_decision_log (append-only skip/fire decisions).

Usage:
    DATABASE_URL=... python migrate_escalation_decision_log.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (timestamptz / jsonb / uuid FK).
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS escalation_decision_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision VARCHAR(16) NOT NULL,
    reason VARCHAR(64) NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    telegram_chat_id BIGINT NOT NULL,
    group_title TEXT,
    telegram_user_id BIGINT,
    role VARCHAR(16),
    telegram_message_id BIGINT,
    trigger_messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    episode_id UUID REFERENCES escalation_episodes(id) ON DELETE SET NULL,
    escalation_event_id BIGINT REFERENCES escalation_events(id) ON DELETE SET NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_esc_dec_chat_created_at
    ON escalation_decision_log (telegram_chat_id, created_at);
CREATE INDEX IF NOT EXISTS ix_esc_dec_decision_created_at
    ON escalation_decision_log (decision, created_at);
CREATE INDEX IF NOT EXISTS ix_esc_dec_reason_created_at
    ON escalation_decision_log (reason, created_at);
"""


def ensure_escalation_decision_log(engine=None) -> None:
    if engine is None:
        engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))


if __name__ == "__main__":
    ensure_escalation_decision_log()
    print("escalation_decision_log table is ready.")
