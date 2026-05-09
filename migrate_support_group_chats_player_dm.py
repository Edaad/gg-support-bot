"""Extend support_group_chats for MTProto outgoing /gc (admin DM with player).

Usage:
    DATABASE_URL=... python migrate_support_group_chats_player_dm.py

Idempotent: safe to run multiple times.

PostgreSQL only (partial unique index, timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

RENAME_INITIAL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'support_group_chats'
      AND column_name = 'initial_message_sent'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'support_group_chats'
      AND column_name = 'initial_group_message_sent'
  ) THEN
    ALTER TABLE support_group_chats RENAME COLUMN initial_message_sent TO initial_group_message_sent;
  END IF;
END $$;
"""

RENAME_ERROR = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'support_group_chats'
      AND column_name = 'error_message'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'support_group_chats'
      AND column_name = 'last_error_message'
  ) THEN
    ALTER TABLE support_group_chats RENAME COLUMN error_message TO last_error_message;
  END IF;
END $$;
"""

NULLABLE_CREATED_BY = """
ALTER TABLE support_group_chats
  ALTER COLUMN created_by_telegram_user_id DROP NOT NULL;
"""

ADD_COLUMNS = [
    """
    ALTER TABLE support_group_chats
      ADD COLUMN IF NOT EXISTS player_telegram_user_id BIGINT;
    """,
    """
    ALTER TABLE support_group_chats
      ADD COLUMN IF NOT EXISTS player_username TEXT;
    """,
    """
    ALTER TABLE support_group_chats
      ADD COLUMN IF NOT EXISTS player_display_name TEXT;
    """,
    """
    ALTER TABLE support_group_chats
      ADD COLUMN IF NOT EXISTS player_dm_status TEXT;
    """,
]

INDEX_PLAYER = """
CREATE INDEX IF NOT EXISTS ix_support_group_chats_player_telegram_user_id
  ON support_group_chats (player_telegram_user_id);
"""

# One support group per (club, player) for DM /gc rows (legacy rows may have NULL player).
PARTIAL_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_group_chats_club_player
  ON support_group_chats (club_key, player_telegram_user_id)
  WHERE player_telegram_user_id IS NOT NULL;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(RENAME_INITIAL))
        conn.execute(text(RENAME_ERROR))
        try:
            conn.execute(text(NULLABLE_CREATED_BY))
        except Exception:
            pass
        for stmt in ADD_COLUMNS:
            conn.execute(text(stmt))
        conn.execute(text(INDEX_PLAYER))
        conn.execute(text(PARTIAL_UNIQUE))
        conn.commit()
        print("support_group_chats player-DM columns and indexes are ready.")
