"""Move rotation variants (Cashapp Stripe, Cashapp Account 1) from Under to Over amount tier.

Run once with DATABASE_URL set:
  python migrate_variants_to_over_tier.py
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from db.connection import get_db


def _find_tier(session, method_id: int, pattern: str):
    return session.execute(
        text(
            """
            SELECT id, label FROM payment_method_tiers
            WHERE method_id = :mid AND label ILIKE :pat
            ORDER BY sort_order, id
            LIMIT 1
            """
        ),
        {"mid": method_id, "pat": pattern},
    ).first()


def main() -> None:
    moved = 0
    with get_db() as session:
        methods = session.execute(
            text(
                """
                SELECT DISTINCT pm.id, pm.name, c.name AS club
                FROM payment_methods pm
                JOIN clubs c ON c.id = pm.club_id
                JOIN payment_method_tiers pmt ON pmt.method_id = pm.id
                WHERE pm.slug = 'cashapp' AND pm.direction = 'deposit'
                """
            )
        ).fetchall()

        for method_id, method_name, club in methods:
            under = _find_tier(session, method_id, "%under%")
            over = _find_tier(session, method_id, "%over%")
            if not under or not over:
                continue

            rows = session.execute(
                text(
                    """
                    SELECT id, label FROM method_variants
                    WHERE tier_id = :under_id
                      AND (
                        TRIM(LOWER(label)) IN ('cashapp stripe', 'cashapp account 1')
                        OR label ILIKE 'cashapp stripe%'
                        OR label ILIKE 'cashapp account%'
                      )
                    """
                ),
                {"under_id": under[0]},
            ).fetchall()

            for variant_id, label in rows:
                session.execute(
                    text("UPDATE method_variants SET tier_id = :over_id WHERE id = :vid"),
                    {"over_id": over[0], "vid": variant_id},
                )
                moved += 1
                print(f"  {club} / {method_name}: {label!r} -> {over[1]} (tier {over[0]})")

    print(f"Done. Moved {moved} variant(s).")


if __name__ == "__main__":
    main()
