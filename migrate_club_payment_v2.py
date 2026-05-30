"""Create greenfield v2 payment config tables (club_payment_*).

Run once with DATABASE_URL set:
  python migrate_club_payment_v2.py

Idempotent: CREATE TABLE IF NOT EXISTS. Does not touch legacy payment_methods tables.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import init_engine

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS club_payment_methods (
        id SERIAL PRIMARY KEY,
        club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
        direction VARCHAR(10) NOT NULL CHECK (direction IN ('deposit', 'cashout')),
        name VARCHAR(50) NOT NULL,
        slug VARCHAR(50) NOT NULL,
        min_amount NUMERIC(12, 2),
        max_amount NUMERIC(12, 2),
        has_sub_options BOOLEAN NOT NULL DEFAULT FALSE,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        sort_order INTEGER NOT NULL DEFAULT 0,
        deposit_limit NUMERIC(12, 2),
        accumulated_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_cpm_club_direction_slug UNIQUE (club_id, direction, slug),
        CONSTRAINT ck_cpm_amount_range CHECK (
            min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_payment_tiers (
        id SERIAL PRIMARY KEY,
        method_id INTEGER NOT NULL REFERENCES club_payment_methods(id) ON DELETE CASCADE,
        label VARCHAR(50) NOT NULL,
        min_amount NUMERIC(12, 2),
        max_amount NUMERIC(12, 2),
        sort_order INTEGER NOT NULL DEFAULT 0,
        response_type VARCHAR(10) NOT NULL DEFAULT 'text',
        response_text TEXT,
        response_file_id TEXT,
        response_caption TEXT,
        use_group_checkout_link BOOLEAN NOT NULL DEFAULT FALSE,
        group_checkout_provider VARCHAR(32),
        hyperlink_text VARCHAR(64),
        checkout_min_amount NUMERIC(12, 2),
        checkout_max_amount NUMERIC(12, 2),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_cpt_method_label UNIQUE (method_id, label),
        CONSTRAINT ck_cpt_amount_range CHECK (
            min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_payment_tier_variants (
        id SERIAL PRIMARY KEY,
        method_id INTEGER NOT NULL REFERENCES club_payment_methods(id) ON DELETE CASCADE,
        tier_id INTEGER NOT NULL REFERENCES club_payment_tiers(id) ON DELETE CASCADE,
        label VARCHAR(100) NOT NULL,
        weight INTEGER NOT NULL DEFAULT 1 CHECK (weight >= 1),
        sort_order INTEGER NOT NULL DEFAULT 0,
        response_type VARCHAR(10) NOT NULL DEFAULT 'text',
        response_text TEXT,
        response_file_id TEXT,
        response_caption TEXT,
        use_group_checkout_link BOOLEAN,
        group_checkout_provider VARCHAR(32),
        hyperlink_text VARCHAR(64),
        checkout_min_amount NUMERIC(12, 2),
        checkout_max_amount NUMERIC(12, 2),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_cptv_tier_label UNIQUE (tier_id, label)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_payment_sub_options (
        id SERIAL PRIMARY KEY,
        method_id INTEGER NOT NULL REFERENCES club_payment_methods(id) ON DELETE CASCADE,
        name VARCHAR(50) NOT NULL,
        slug VARCHAR(50) NOT NULL,
        response_type VARCHAR(10) NOT NULL DEFAULT 'text',
        response_text TEXT,
        response_file_id TEXT,
        response_caption TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_cpso_method_slug UNIQUE (method_id, slug)
    )
    """,
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_cpm_club_direction_active ON club_payment_methods (club_id, direction, is_active, sort_order)",
    "CREATE INDEX IF NOT EXISTS ix_cpt_method_sort ON club_payment_tiers (method_id, sort_order, id)",
    "CREATE INDEX IF NOT EXISTS ix_cptv_tier_sort ON club_payment_tier_variants (tier_id, sort_order, id)",
]


def main() -> None:
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in TABLES:
            conn.execute(text(stmt))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
    print("club_payment_* v2 tables and indexes are ready.")


if __name__ == "__main__":
    main()
