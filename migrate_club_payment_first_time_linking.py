"""Add per-method first-time deposit linking settings to club_payment_methods.

Run once with DATABASE_URL set:
  python migrate_club_payment_first_time_linking.py

Idempotent. Seeds Creator Club + Round Table venmo/zelle from legacy test-bot defaults.
"""

from __future__ import annotations

from sqlalchemy import text

from db.connection import get_db, init_engine

DDL = """
ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS first_time_linking_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS first_time_bind_mode VARCHAR(32);
"""

# club name (lower) -> { slug -> bind_mode }
_SEED_BIND_MODES: dict[str, dict[str, str]] = {
    "creator club": {"venmo": "special_amount", "zelle": "special_amount"},
    "round table": {"venmo": "memo_emoji", "zelle": "memo_emoji"},
}


def _seed_defaults() -> None:
    with get_db() as session:
        clubs = session.execute(text("SELECT id, name FROM clubs")).fetchall()
        name_to_id = {
            " ".join((row[1] or "").strip().lower().split()): int(row[0])
            for row in clubs
            if row[1]
        }
        for club_key, slug_modes in _SEED_BIND_MODES.items():
            club_id = name_to_id.get(club_key)
            if club_id is None:
                continue
            for slug, mode in slug_modes.items():
                session.execute(
                    text(
                        """
                        UPDATE club_payment_methods
                        SET first_time_linking_enabled = TRUE,
                            first_time_bind_mode = :mode
                        WHERE club_id = :club_id
                          AND direction = 'deposit'
                          AND slug = :slug
                          AND (
                            first_time_linking_enabled IS NOT TRUE
                            OR first_time_bind_mode IS NULL
                          )
                        """
                    ),
                    {"club_id": club_id, "slug": slug, "mode": mode},
                )


def main() -> None:
    init_engine()
    with get_db() as session:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                session.execute(text(s))
    _seed_defaults()
    print("club_payment_methods.first_time_linking_* columns are ready.")


if __name__ == "__main__":
    main()
