"""Delete all union methods and related manual deposit rows (clean slate).

Run once before migrate_union_method_shape.py when reshaping union methods.

Usage:
    DATABASE_URL=... python migrate_union_methods_clean_slate.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

DELETE_DEPOSITS = """
DELETE FROM manual_deposit_requests
WHERE method_id IN (
    SELECT id FROM club_payment_methods
    WHERE tracks_manual_requests = true
)
"""

DELETE_JUNCTION = """
DELETE FROM club_payment_method_clubs
WHERE method_id IN (
    SELECT id FROM club_payment_methods
    WHERE tracks_manual_requests = true
)
"""

DELETE_METHODS = """
DELETE FROM club_payment_methods
WHERE tracks_manual_requests = true
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        dep = conn.execute(text(DELETE_DEPOSITS))
        junc = conn.execute(text(DELETE_JUNCTION))
        methods = conn.execute(text(DELETE_METHODS))
    print(
        "migrate_union_methods_clean_slate: done "
        f"(deposits={dep.rowcount}, junction={junc.rowcount}, methods={methods.rowcount})"
    )


if __name__ == "__main__":
    main()
