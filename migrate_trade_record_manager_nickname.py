"""Add manager_nickname to trade_record_lines (trade record column D).

Usage:
    DATABASE_URL=... python migrate_trade_record_manager_nickname.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
ALTER TABLE trade_record_lines
ADD COLUMN IF NOT EXISTS manager_nickname VARCHAR(255);
"""

with engine.connect() as conn:
    conn.execute(text(DDL))
    conn.commit()
    print("trade_record_lines.manager_nickname is ready.")
