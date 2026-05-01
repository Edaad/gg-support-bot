"""Add ``member_join_*`` columns on ``clubs`` (preamble text + TOS PDF file_id).

Usage:
    DATABASE_URL=... python migrate_member_join_fields.py

PostgreSQL. Idempotent ADD COLUMN IF NOT EXISTS.
"""

from sqlalchemy import text

from db.connection import init_engine

STMTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS member_join_preamble_text TEXT;",
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS member_join_tos_file_id TEXT;",
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS member_join_tos_caption TEXT;",
]


if __name__ == "__main__":
    engine = init_engine()

    with engine.connect() as conn:
        for s in STMTS:
            conn.execute(text(s))
        conn.commit()
    print("clubs member_join_* columns are ready.")
