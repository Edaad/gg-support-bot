"""Rewrite Zapier bank labels in Zelle tables to configured recipient emails/phones.

Maps ingested bank account labels (from Zapier) to the Zelle destinations used in
club payment tier variants so auto-bind and payer bindings match correctly.

Usage:
    DATABASE_URL=... python migrate_zelle_bank_recipient_labels.py          # dry-run
    DATABASE_URL=... python migrate_zelle_bank_recipient_labels.py --apply

Idempotent: rows already at the target recipient are skipped.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict

from sqlalchemy import text

from bot.services.payment_method_binding import normalize_zelle_recipient
from db.connection import init_engine

# Source label (case-insensitive) -> normalized Zelle destination
BANK_LABEL_TO_RECIPIENT: dict[str, str] = {
    "pnc bank": "coachingg444@gmail.com",
    "us bank": "playsocialgg@gmail.com",
    "clubgto well's fargo": "2133729202",
    "bailey's wells fargo": "3105670961",
}


def _label_key(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", s)


def _target_for(raw: str) -> str | None:
    key = _label_key(raw)
    mapped = BANK_LABEL_TO_RECIPIENT.get(key)
    if not mapped:
        return None
    return normalize_zelle_recipient(mapped)


def _collect_updates(rows: list[tuple]) -> list[dict]:
    updates: list[dict] = []
    for row_id, recipient in rows:
        target = _target_for(recipient)
        if not target:
            continue
        current = normalize_zelle_recipient(recipient)
        if current == target:
            continue
        updates.append(
            {
                "id": row_id,
                "from": recipient,
                "to": target,
            }
        )
    return updates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates (default is dry-run only)",
    )
    args = parser.parse_args()

    engine = init_engine()
    with engine.connect() as conn:
        all_payments = conn.execute(
            text("SELECT id, zelle_recipient FROM zelle_payments")
        ).fetchall()
        payment_by_id = {r[0]: r[1] for r in all_payments}
        payment_updates = _collect_updates(
            [(i, payment_by_id[i]) for i in payment_by_id if _target_for(payment_by_id[i])],
        )

        payer_rows = conn.execute(
            text("SELECT id, zelle_recipient FROM zelle_payer_bindings")
        ).fetchall()
        payer_updates = _collect_updates(payer_rows)

        binding_rows = conn.execute(
            text(
                "SELECT id, venmo_handle FROM group_payment_method_bindings "
                "WHERE payment_method_slug = 'zelle'"
            )
        ).fetchall()
        binding_updates = _collect_updates(binding_rows)

        summary: dict[str, list[dict]] = {
            "zelle_payments": payment_updates,
            "zelle_payer_bindings": payer_updates,
            "group_payment_method_bindings": binding_updates,
        }

        print("Zelle bank label -> recipient migration")
        print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
        print()
        for table, updates in summary.items():
            print(f"{table}: {len(updates)} row(s) to update")
            by_target: dict[str, int] = defaultdict(int)
            for u in updates:
                by_target[f"{_label_key(u['from'])} -> {u['to']}"] += 1
            for label, count in sorted(by_target.items()):
                print(f"  {label}: {count}")

        if not args.apply:
            print()
            print("No changes written. Re-run with --apply to update.")
            return

    total = 0
    with engine.begin() as conn:
        for u in payment_updates:
            conn.execute(
                text(
                    "UPDATE zelle_payments SET zelle_recipient = :to, "
                    "updated_at = NOW() WHERE id = :id"
                ),
                {"id": u["id"], "to": u["to"]},
            )
            total += 1
        for u in payer_updates:
            conn.execute(
                text(
                    "UPDATE zelle_payer_bindings SET zelle_recipient = :to "
                    "WHERE id = :id"
                ),
                {"id": u["id"], "to": u["to"]},
            )
            total += 1
        for u in binding_updates:
            conn.execute(
                text(
                    "UPDATE group_payment_method_bindings SET venmo_handle = :to "
                    "WHERE id = :id"
                ),
                {"id": u["id"], "to": u["to"]},
            )
            total += 1

    print()
    print(f"Applied {total} update(s).")


if __name__ == "__main__":
    main()
