"""Read-only: show which tier and Venmo handles a deposit amount routes to.

Run with DATABASE_URL set:
  python scripts/show_tier_routing.py 11 23
"""

from __future__ import annotations

import sys
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

from bot.services.payment_tier_order import select_tier_for_amount
from db.connection import get_db
from db.models import Club, ClubPaymentMethod

AMOUNTS = ("50", "100", "150", "2000", "9999", "12000")


def main(method_ids: list[int]) -> None:
    with get_db() as session:
        clubs = {c.id: c.name for c in session.query(Club).all()}
        for method_id in method_ids:
            method = session.get(ClubPaymentMethod, method_id)
            if method is None:
                print(f"method {method_id}: not found")
                continue
            tiers = list(method.tiers)
            print(f"{clubs.get(method.club_id)} {method.slug} (method {method_id})")
            for raw in AMOUNTS:
                tier = select_tier_for_amount(tiers, Decimal(raw))
                if tier is None:
                    print(f"  ${raw:>6}: no tier (method hidden)")
                    continue
                handles = sorted(
                    (v.venmo_tag or v.label)
                    for v in tier.variants
                    if (v.weight or 0) > 0
                )
                print(f"  ${raw:>6}: {tier.label!r} -> {handles}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [11, 23])
