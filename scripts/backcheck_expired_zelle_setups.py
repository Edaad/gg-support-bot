#!/usr/bin/env python3
"""Backcheck expired/cancelled Zelle special-amount setup attempts against payments.

When a setup attempt expires or is cancelled before auto-bind runs (e.g. bank-label
recipient mismatch, or manual bind cancels the pending attempt), a matching payment
may still exist in the attempt window. This script finds those payments and
optionally retroactively completes the attempt stats.

Usage:
    DATABASE_URL=... python scripts/backcheck_expired_zelle_setups.py
    DATABASE_URL=... python scripts/backcheck_expired_zelle_setups.py --apply
    DATABASE_URL=... python scripts/backcheck_expired_zelle_setups.py --apply --csv backups/expired_zelle_backcheck.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from sqlalchemy import text

from bot.services.payment_binding_events import record_payment_bound
from bot.services.payment_method_binding import (
    ATTEMPT_STATUS_SUCCEEDED,
    BOUND_VIA_BACKFILL,
    _variant_zelle_recipient_matches,
    record_group_binding_in_session,
)
from bot.services.venmo_payments import normalize_payer_name, resolve_display_group_title
from bot.services.zelle_payments import _apply_binding_to_payment, _upsert_payer_binding
from db.connection import get_db
from db.models import GroupPaymentMethodBinding, PaymentMethodBindAttempt, ZellePayment


def _player_token_from_title(title: str | None) -> str:
    parts = [p.strip() for p in (title or "").split("/") if p.strip()]
    if not parts:
        return ""
    raw = parts[-1].lstrip("@")
    return normalize_payer_name(raw).split()[0] if raw else ""


def payer_matches_group(payer_name: str, group_title: str | None) -> bool:
    token = _player_token_from_title(group_title)
    payer = normalize_payer_name(payer_name)
    if not token or not payer:
        return False
    return token in payer or payer.split()[0] in token


def _group_title(session, chat_id: int) -> str | None:
    row = session.execute(
        text("SELECT name FROM groups WHERE chat_id = :c LIMIT 1"),
        {"c": chat_id},
    ).scalar()
    if row:
        return str(row)
    return resolve_display_group_title(chat_id)


def _match_payments(session, attempt: PaymentMethodBindAttempt) -> list[ZellePayment]:
    if attempt.amount_cents is None:
        return []
    rows = (
        session.query(ZellePayment)
        .filter(
            ZellePayment.amount_cents == int(attempt.amount_cents),
            ZellePayment.created_at >= attempt.created_at,
            ZellePayment.created_at <= attempt.expires_at,
        )
        .order_by(ZellePayment.created_at)
        .all()
    )
    return [
        p
        for p in rows
        if _variant_zelle_recipient_matches(
            session, int(attempt.variant_id), p.zelle_recipient
        )
    ]


def _classify(
    attempt: PaymentMethodBindAttempt,
    matches: list[ZellePayment],
    group_title: str | None,
) -> tuple[str, ZellePayment | None]:
    if not matches:
        return "no_payment", None
    same_chat = [
        p
        for p in matches
        if p.telegram_chat_id is not None
        and int(p.telegram_chat_id) == int(attempt.telegram_chat_id)
    ]
    if same_chat:
        return "already_bound_same_chat", same_chat[0]
    unbound = [p for p in matches if p.telegram_chat_id is None]
    if len(unbound) == 1 and payer_matches_group(unbound[0].payer_name, group_title):
        return "unbound_name_match", unbound[0]
    if len(unbound) == 1:
        return "unbound_ambiguous", unbound[0]
    if len(unbound) > 1:
        return "unbound_multiple", None
    bound_elsewhere = [
        p
        for p in matches
        if p.telegram_chat_id is not None
        and int(p.telegram_chat_id) != int(attempt.telegram_chat_id)
    ]
    if bound_elsewhere:
        return "bound_other_chat", bound_elsewhere[0]
    return "ambiguous", matches[0]


def _complete_attempt(session, attempt: PaymentMethodBindAttempt, payment: ZellePayment) -> None:
    attempt.status = ATTEMPT_STATUS_SUCCEEDED
    attempt.zelle_payment_id = int(payment.id)
    attempt.completed_at = payment.bound_at or payment.created_at or datetime.now(timezone.utc)


def _bind_payment_to_attempt(
    session,
    *,
    attempt: PaymentMethodBindAttempt,
    payment: ZellePayment,
    group_title: str,
) -> None:
    live_title = resolve_display_group_title(int(attempt.telegram_chat_id)) or group_title
    _apply_binding_to_payment(
        payment,
        telegram_chat_id=int(attempt.telegram_chat_id),
        club_id=int(attempt.club_id),
        bound_group_title_at_bind=live_title[:255],
        auto_bound=True,
        bound_by_telegram_user_id=None,
    )
    _upsert_payer_binding(
        session,
        payer_name=payment.payer_name,
        zelle_recipient=payment.zelle_recipient,
        telegram_chat_id=int(attempt.telegram_chat_id),
        club_id=int(attempt.club_id),
        bound_group_title_at_bind=live_title[:255],
        bound_by_telegram_user_id=None,
    )
    binding = (
        session.query(GroupPaymentMethodBinding)
        .filter_by(
            telegram_chat_id=int(attempt.telegram_chat_id),
            payment_method_slug="zelle",
        )
        .one_or_none()
    )
    if binding is None:
        record_group_binding_in_session(
            session,
            telegram_chat_id=int(attempt.telegram_chat_id),
            club_id=int(attempt.club_id),
            payment_method_slug="zelle",
            bound_via=BOUND_VIA_BACKFILL,
            variant_id=int(attempt.variant_id),
            venmo_handle=payment.zelle_recipient,
            first_bind_attempt_id=int(attempt.id),
        )
    else:
        if binding.variant_id is None:
            binding.variant_id = int(attempt.variant_id)
        if binding.first_bind_attempt_id is None:
            binding.first_bind_attempt_id = int(attempt.id)
        if binding.bound_via in ("manual_notification", "manual_dashboard"):
            pass
        elif binding.bound_via != BOUND_VIA_BACKFILL:
            binding.bound_via = BOUND_VIA_BACKFILL
    _complete_attempt(session, attempt, payment)
    record_payment_bound(
        payment_method_slug="zelle",
        payment_id=int(payment.id),
        telegram_chat_id=int(attempt.telegram_chat_id),
        club_id=int(attempt.club_id),
        bound_group_title=live_title,
        auto_bound=True,
        bound_via=BOUND_VIA_BACKFILL,
        bind_attempt_id=int(attempt.id),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply retroactive attempt completion and unbound binds",
    )
    parser.add_argument("--csv", type=Path, help="Write results CSV to this path")
    args = parser.parse_args()

    rows_out: list[dict] = []
    applied = 0

    with get_db() as session:
        attempts = (
            session.query(PaymentMethodBindAttempt)
            .filter(
                PaymentMethodBindAttempt.payment_method_slug == "zelle",
                PaymentMethodBindAttempt.bind_kind == "special_amount",
                PaymentMethodBindAttempt.status.in_(("expired", "cancelled")),
            )
            .order_by(PaymentMethodBindAttempt.created_at.desc())
            .all()
        )

        for attempt in attempts:
            title = _group_title(session, int(attempt.telegram_chat_id))
            matches = _match_payments(session, attempt)
            outcome, payment = _classify(attempt, matches, title)
            action = "none"
            if args.apply and payment is not None:
                if outcome == "already_bound_same_chat":
                    _complete_attempt(session, attempt, payment)
                    binding = (
                        session.query(GroupPaymentMethodBinding)
                        .filter_by(
                            telegram_chat_id=int(attempt.telegram_chat_id),
                            payment_method_slug="zelle",
                        )
                        .one_or_none()
                    )
                    if binding is not None:
                        if binding.variant_id is None:
                            binding.variant_id = int(attempt.variant_id)
                        if binding.first_bind_attempt_id is None:
                            binding.first_bind_attempt_id = int(attempt.id)
                    action = "attempt_succeeded"
                    applied += 1
                elif outcome == "unbound_name_match":
                    _bind_payment_to_attempt(
                        session,
                        attempt=attempt,
                        payment=payment,
                        group_title=title or "",
                    )
                    action = "bound_payment"
                    applied += 1

            row = {
                "attempt_id": attempt.id,
                "telegram_chat_id": attempt.telegram_chat_id,
                "group_title": title or "",
                "amount_cents": attempt.amount_cents,
                "attempt_created_at": attempt.created_at.isoformat()
                if attempt.created_at
                else "",
                "outcome": outcome,
                "payment_id": payment.id if payment else "",
                "payer_name": payment.payer_name if payment else "",
                "payment_created_at": payment.created_at.isoformat()
                if payment and payment.created_at
                else "",
                "payment_chat_id": payment.telegram_chat_id if payment else "",
                "action": action,
            }
            rows_out.append(row)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else [])
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"Wrote {len(rows_out)} rows to {args.csv}")

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Unresolved attempts checked: {len(rows_out)}")
    by_outcome: dict[str, int] = {}
    for r in rows_out:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    for k, v in sorted(by_outcome.items()):
        print(f"  {k}: {v}")
    if args.apply:
        print(f"Applied {applied} update(s).")
    print()
    for r in rows_out:
        if r["outcome"] == "no_payment":
            continue
        amt = (int(r["amount_cents"]) / 100) if r["amount_cents"] else 0
        pay = f"pay#{r['payment_id']} {r['payer_name']!r}" if r["payment_id"] else ""
        print(
            f"#{r['attempt_id']} {r['group_title']!r} ${amt:.2f} "
            f"[{r['outcome']}] {pay} action={r['action']}"
        )


if __name__ == "__main__":
    main()
