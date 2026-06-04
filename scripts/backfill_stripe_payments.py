#!/usr/bin/env python3
"""Backfill stripe_checkout_sessions from Stripe API + optional CSV (group title links).

The dashboard Payments page only shows rows inserted when checkout.session.completed
webhooks ran successfully. Payments that completed on Stripe before the webhook was
configured (or when metadata was missing) will not appear until backfilled.

Export your Numbers sheet as CSV (File → Export To → CSV). Expected columns include
Payment Intent ID (pi_…) and optionally group_title for rows missing session metadata.

Usage:
    # Preview rows from CSV (matches your payments_main_linked export)
    STRIPE_SECRET_KEY=... DATABASE_URL=... python scripts/backfill_stripe_payments.py \\
        --csv ~/Downloads/payments_main_linked.csv --dry-run

    # Apply backfill for linked rows
    python scripts/backfill_stripe_payments.py --csv ~/Downloads/payments_main_linked.csv --apply

    # Backfill all completed Checkout Sessions in a created range (no CSV)
    python scripts/backfill_stripe_payments.py --from-stripe --created-gte 2026-05-01 --apply

    # Single payment intent
    python scripts/backfill_stripe_payments.py --payment-intent pi_3TdMzp05pIynMQrD1a8s4bA9 --apply
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

load_dotenv()

import stripe
from sqlalchemy.orm import Session

from bot.services.club import find_group_chat_id_by_name
from bot.services.player_details import (
    parse_tracking_title,
    resolve_club_id_from_shorthand,
)
from bot.services.stripe_deposit import (
    record_completed_checkout_payment,
    resolve_stripe_secret_key,
    stripe_configured,
)
from db.connection import get_db, init_engine
from db.models import StripeCheckoutSession, StripeCustomer


def _normalize_header(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def _stripe_client() -> None:
    key = resolve_stripe_secret_key()
    if not key:
        raise SystemExit("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key


def _session_dict(session: Any) -> dict[str, Any]:
    if hasattr(session, "to_dict"):
        return session.to_dict()
    if isinstance(session, dict):
        return session
    raise TypeError(f"unexpected Stripe session type: {type(session)!r}")


def _resolve_group_to_ids(group_title: str) -> tuple[Optional[int], Optional[int]]:
    cleaned = (group_title or "").strip()
    if not cleaned:
        return None, None
    parsed = parse_tracking_title(cleaned)
    if not parsed:
        return None, None
    shorthand, _ = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if club_id is None:
        return None, None
    chat_id = find_group_chat_id_by_name(int(club_id), cleaned)
    return chat_id, int(club_id)


def enrich_checkout_dict(
    checkout: dict[str, Any],
    db: Session,
    *,
    group_title: Optional[str] = None,
) -> dict[str, Any]:
    """Ensure metadata has telegram_chat_id, club_id, and customer for DB insert."""
    meta = dict(checkout.get("metadata") or {})
    chat_id: Optional[int] = None
    club_id: Optional[int] = None

    raw_chat = meta.get("telegram_chat_id")
    raw_club = meta.get("club_id")
    if raw_chat not in (None, ""):
        try:
            chat_id = int(raw_chat)
        except (TypeError, ValueError):
            chat_id = None
    if raw_club not in (None, ""):
        try:
            club_id = int(raw_club)
        except (TypeError, ValueError):
            club_id = None

    if chat_id is None and checkout.get("client_reference_id"):
        try:
            chat_id = int(checkout["client_reference_id"])
        except (TypeError, ValueError):
            pass

    cust_id = str(checkout.get("customer") or "").strip()
    if cust_id and (chat_id is None or club_id is None):
        row = (
            db.query(StripeCustomer)
            .filter(StripeCustomer.stripe_customer_id == cust_id)
            .one_or_none()
        )
        if row is not None:
            if chat_id is None:
                chat_id = int(row.telegram_chat_id)
            if club_id is None:
                club_id = int(row.club_id)

    if group_title and (chat_id is None or club_id is None):
        g_chat, g_club = _resolve_group_to_ids(group_title)
        if g_chat is not None:
            chat_id = g_chat
        if g_club is not None:
            club_id = g_club

    if chat_id is not None:
        meta["telegram_chat_id"] = str(chat_id)
    if club_id is not None:
        meta["club_id"] = str(club_id)
    checkout["metadata"] = meta
    return checkout


def fetch_checkout_for_payment_intent(payment_intent_id: str) -> Optional[dict[str, Any]]:
    """Load a completed checkout session dict from Stripe for a PaymentIntent id."""
    pi_id = (payment_intent_id or "").strip()
    if not pi_id.startswith("pi_"):
        return None

    pi = stripe.PaymentIntent.retrieve(pi_id)
    cs_id = getattr(pi, "checkout_session", None) or (pi.get("checkout_session") if isinstance(pi, dict) else None)

    if cs_id:
        session = stripe.checkout.Session.retrieve(str(cs_id))
        return _session_dict(session)

    listed = stripe.checkout.Session.list(payment_intent=pi_id, limit=1)
    data = listed.data if hasattr(listed, "data") else listed.get("data", [])
    if data:
        return _session_dict(data[0])

    cust_id = str(getattr(pi, "customer", None) or (pi.get("customer") if isinstance(pi, dict) else "") or "").strip()
    if not cust_id:
        return None

    amount = getattr(pi, "amount_received", None) or getattr(pi, "amount", None)
    if amount is None and isinstance(pi, dict):
        amount = pi.get("amount_received") or pi.get("amount")

    meta = getattr(pi, "metadata", None) or (pi.get("metadata") if isinstance(pi, dict) else {}) or {}
    return {
        "id": f"backfill_{pi_id}",
        "customer": cust_id,
        "amount_total": int(amount or 0),
        "currency": str(getattr(pi, "currency", None) or (pi.get("currency") if isinstance(pi, dict) else "usd")),
        "payment_intent": pi_id,
        "metadata": dict(meta),
        "status": "complete",
    }


def fetch_checkout_session(session_id: str) -> Optional[dict[str, Any]]:
    sid = (session_id or "").strip()
    if not sid.startswith("cs_"):
        return None
    session = stripe.checkout.Session.retrieve(sid)
    return _session_dict(session)


def already_recorded(db: Session, session_id: str) -> bool:
    return (
        db.query(StripeCheckoutSession)
        .filter(
            StripeCheckoutSession.stripe_checkout_session_id == session_id,
            StripeCheckoutSession.status == "complete",
        )
        .first()
        is not None
    )


def backfill_one(
    db: Session,
    checkout: dict[str, Any],
    *,
    group_title: Optional[str] = None,
    dry_run: bool,
) -> tuple[str, str]:
    """Returns (status, detail) where status is skipped|would_insert|inserted|failed."""
    session_id = str(checkout.get("id") or "").strip()
    if not session_id:
        return "failed", "missing session id"

    status = str(checkout.get("status") or "").strip().lower()
    if status and status != "complete":
        return "skipped", f"session status={status!r}"

    if already_recorded(db, session_id):
        return "skipped", "already in dashboard DB"

    enriched = enrich_checkout_dict(checkout, db, group_title=group_title)
    meta = enriched.get("metadata") or {}
    if not meta.get("telegram_chat_id") or not meta.get("club_id") or not enriched.get("customer"):
        return "failed", "missing chat_id/club_id/customer (add group_title column?)"

    if dry_run:
        return "would_insert", (
            f"session={session_id} chat={meta.get('telegram_chat_id')} "
            f"club={meta.get('club_id')} amount={enriched.get('amount_total')}"
        )

    ok = record_completed_checkout_payment(enriched)
    if ok:
        return "inserted", session_id
    return "skipped", "record_completed_checkout_payment returned false (duplicate?)"


def _rows_from_header_matrix(header: list[str], data_rows: list[list[str]]) -> list[dict[str, str]]:
    norm_headers = [_normalize_header(h) for h in header]
    out: list[dict[str, str]] = []
    for cells in data_rows:
        row: dict[str, str] = {}
        for i, norm in enumerate(norm_headers):
            if not norm:
                continue
            val = cells[i] if i < len(cells) else ""
            row[norm] = str(val).strip() if val is not None else ""
        out.append(row)
    return out


def load_numbers_rows(path: str) -> list[dict[str, str]]:
    try:
        from numbers_parser import Document
    except ImportError as e:
        raise SystemExit(
            "Reading .numbers requires: pip install numbers-parser\n"
            "Or export the sheet to CSV from Numbers (File → Export To → CSV)."
        ) from e

    doc = Document(path)
    table = doc.sheets[0].tables[0]
    rows = list(table.iter_rows())
    if not rows:
        return []
    header = [str(c.value or "") for c in rows[0]]
    data = [[str(c.value or "") for c in r] for r in rows[1:]]
    return _rows_from_header_matrix(header, data)


def load_csv_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        norm_map = {_normalize_header(h): h for h in reader.fieldnames}
        rows: list[dict[str, str]] = []
        for raw in reader:
            row: dict[str, str] = {}
            for norm, orig in norm_map.items():
                row[norm] = (raw.get(orig) or "").strip()
            rows.append(row)
        return rows


def load_input_rows(path: str) -> list[dict[str, str]]:
    expanded = os.path.expanduser(path)
    if expanded.lower().endswith(".numbers"):
        return load_numbers_rows(expanded)
    return load_csv_rows(expanded)


def iter_csv_targets(rows: list[dict[str, str]]):
    for row in rows:
        pi = (
            row.get("payment_intent_id")
            or row.get("paymentintent_id")
            or row.get("payment_intent")
            or ""
        ).strip()
        cs = (
            row.get("stripe_checkout_session_id")
            or row.get("checkout_session_id")
            or row.get("payment_link_id")
            or ""
        ).strip()
        if cs and not cs.startswith("cs_"):
            cs = ""
        title = (row.get("group_title") or row.get("group_titl") or "").strip() or None
        if pi or cs:
            yield pi, cs, title


def backfill_from_stripe_range(*, created_gte: int, created_lte: Optional[int], dry_run: bool) -> int:
    """List paid checkout sessions from Stripe and backfill any missing from DB."""
    params: dict[str, Any] = {
        "limit": 100,
        "status": "complete",
        "created": {"gte": created_gte},
    }
    if created_lte is not None:
        params["created"]["lte"] = created_lte

    inserted = skipped = failed = 0
    with get_db() as db:
        starting_after = None
        while True:
            if starting_after:
                page = stripe.checkout.Session.list(**params, starting_after=starting_after)
            else:
                page = stripe.checkout.Session.list(**params)
            for session in page.data:
                checkout = _session_dict(session)
                status, detail = backfill_one(db, checkout, dry_run=dry_run)
                sid = checkout.get("id", "?")
                print(f"[{status}] {sid} — {detail}")
                if status == "inserted":
                    inserted += 1
                elif status == "would_insert":
                    inserted += 1
                elif status == "failed":
                    failed += 1
                else:
                    skipped += 1
            if not page.has_more:
                break
            starting_after = page.data[-1].id

    print(f"Done: would_insert/inserted={inserted} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill stripe_checkout_sessions for the Payments dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--csv",
        help="CSV or .numbers export from payments_main_linked (or similar)",
    )
    parser.add_argument("--payment-intent", action="append", default=[], metavar="PI")
    parser.add_argument("--checkout-session", action="append", default=[], metavar="CS")
    parser.add_argument(
        "--from-stripe",
        action="store_true",
        help="List all complete Checkout Sessions from Stripe API (ignore CSV)",
    )
    parser.add_argument(
        "--created-gte",
        help="With --from-stripe: ISO date or unix ts for Session.created lower bound",
    )
    parser.add_argument("--created-lte", help="Optional upper bound for Session.created")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, no DB writes (default when --apply is omitted)",
    )
    parser.add_argument("--apply", action="store_true", help="Write to DB")
    args = parser.parse_args()

    if not stripe_configured():
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 1

    init_engine()
    _stripe_client()
    dry_run = not args.apply
    if dry_run:
        print("DRY RUN — pass --apply to insert rows\n")

    if args.from_stripe:
        if not args.created_gte:
            print("--from-stripe requires --created-gte (e.g. 2026-05-01)", file=sys.stderr)
            return 1
        raw = args.created_gte.strip()
        try:
            gte = int(raw)
        except ValueError:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            gte = int(dt.timestamp())
        lte = None
        if args.created_lte:
            raw_lte = args.created_lte.strip()
            try:
                lte = int(raw_lte)
            except ValueError:
                dt = datetime.fromisoformat(raw_lte.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                lte = int(dt.timestamp())
        return backfill_from_stripe_range(created_gte=gte, created_lte=lte, dry_run=dry_run)

    targets: list[tuple[str, str, Optional[str]]] = []
    if args.csv:
        rows = load_input_rows(args.csv)
        targets.extend(iter_csv_targets(rows))
    for pi in args.payment_intent:
        targets.append((pi.strip(), "", None))
    for cs in args.checkout_session:
        targets.append(("", cs.strip(), None))

    if not targets:
        parser.error("Provide --csv, --payment-intent, --checkout-session, or --from-stripe")

    inserted = skipped = failed = 0
    with get_db() as db:
        for pi, cs, group_title in targets:
            checkout = None
            if cs:
                checkout = fetch_checkout_session(cs)
            if checkout is None and pi:
                checkout = fetch_checkout_for_payment_intent(pi)
            if checkout is None:
                label = cs or pi or "?"
                print(f"[failed] {label} — could not load from Stripe")
                failed += 1
                continue

            status, detail = backfill_one(db, checkout, group_title=group_title, dry_run=dry_run)
            label = pi or cs or checkout.get("id", "?")
            print(f"[{status}] {label} — {detail}")
            if status in ("inserted", "would_insert"):
                inserted += 1
            elif status == "failed":
                failed += 1
            else:
                skipped += 1

    print(f"\nDone: would_insert/inserted={inserted} skipped={skipped} failed={failed}")
    if dry_run and inserted:
        print("Re-run with --apply to write missing payments to the dashboard DB.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
