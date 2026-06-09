"""One-off: list crypto from_address rows with both bound and unbound payments."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text

from db.connection import get_db, init_engine


def main() -> None:
    init_engine()
    with get_db() as s:
        dup = s.execute(
            text(
                """
                SELECT from_address,
                       COUNT(*) AS total,
                       COUNT(telegram_chat_id) AS bound,
                       COUNT(*) - COUNT(telegram_chat_id) AS unbound,
                       MAX(created_at)::text AS last_payment
                FROM crypto_payments
                GROUP BY from_address
                HAVING COUNT(*) > 1
                   AND COUNT(telegram_chat_id) > 0
                   AND COUNT(*) > COUNT(telegram_chat_id)
                ORDER BY MAX(created_at) DESC
                LIMIT 10
                """
            )
        ).fetchall()

        print("=== Same wallet: bound + unbound payments ===")
        if not dup:
            print("(none)")
        for r in dup:
            print(
                f"{r.from_address} | total={r.total} bound={r.bound} "
                f"unbound={r.unbound} last={r.last_payment}"
            )

        if dup:
            addr = dup[0].from_address
            print(f"\n=== Detail: {addr} ===")
            rows = s.execute(
                text(
                    """
                    SELECT id, created_at::text, amount_cents, token_symbol, alert_scope,
                           telegram_chat_id, club_id, bound_group_title_at_bind,
                           LEFT(transaction_hash, 18) AS tx
                    FROM crypto_payments
                    WHERE from_address = :a
                    ORDER BY created_at
                    """
                ),
                {"a": addr},
            ).fetchall()
            for r in rows:
                st = "BOUND" if r.telegram_chat_id else "UNBOUND"
                print(
                    f"  id={r.id} {st} scope={r.alert_scope} chat={r.telegram_chat_id} "
                    f"club={r.club_id} title={r.bound_group_title_at_bind!r} "
                    f"amt={r.amount_cents} {r.token_symbol} tx={r.tx}... {r.created_at}"
                )

        print("\n=== Last 10 crypto payments ===")
        recent = s.execute(
            text(
                """
                SELECT id, from_address, telegram_chat_id, bound_group_title_at_bind,
                       alert_scope, amount_cents, token_symbol, created_at::text
                FROM crypto_payments
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
        ).fetchall()
        for r in recent:
            st = "BOUND" if r.telegram_chat_id else "UNBOUND"
            print(
                f"id={r.id} {st} scope={r.alert_scope} from={r.from_address[:34]} "
                f"chat={r.telegram_chat_id} title={r.bound_group_title_at_bind!r} "
                f"amt={r.amount_cents} {r.token_symbol} {r.created_at}"
            )


if __name__ == "__main__":
    main()
