"""Add is_public on club_payment_methods + group_deposit_method_access table.

Run once with DATABASE_URL set:
  python migrate_group_deposit_method_access.py

Idempotent. Existing methods default to is_public=TRUE (no behavior change).
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import get_db, init_engine

DDL = """
ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS group_deposit_method_access (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    club_payment_method_id INTEGER NOT NULL REFERENCES club_payment_methods(id) ON DELETE CASCADE,
    access_type VARCHAR(16) NOT NULL,
    created_by_telegram_user_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_gdma_chat_method UNIQUE (telegram_chat_id, club_payment_method_id),
    CONSTRAINT ck_gdma_access_type CHECK (access_type IN ('blacklist', 'whitelist'))
);

CREATE INDEX IF NOT EXISTS ix_gdma_telegram_chat_id
    ON group_deposit_method_access (telegram_chat_id);

CREATE INDEX IF NOT EXISTS ix_gdma_club_id
    ON group_deposit_method_access (club_id);

CREATE INDEX IF NOT EXISTS ix_gdma_method_id
    ON group_deposit_method_access (club_payment_method_id);
"""


def main() -> None:
    init_engine()
    with get_db() as session:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                session.execute(text(s))
    print(
        "club_payment_methods.is_public and group_deposit_method_access are ready."
    )


if __name__ == "__main__":
    main()
