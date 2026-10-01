"""CRUD helpers for staff_cashout_records, destinations, and money sends."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import exists, or_

from bot.services.club import (
    get_method_by_id,
    get_sub_option_by_id,
)
from bot.services.player_details import parse_tracking_title
from club_gc_settings import get_club_gc_config_by_link_club_id
from db.connection import get_db
from db.models import (
    CashAppPayment,
    Club,
    CryptoPayment,
    PayPalPayment,
    StaffCashoutMoneySend,
    StaffCashoutPayment,
    StaffCashoutRecord,
    StaffCashoutSendProof,
    StripeCheckoutSession,
    VenmoPayment,
    ZellePayment,
)

logger = logging.getLogger(__name__)

STATUSES = ("active", "cleared", "oversent", "do_not_send")
LEDGER_STATUSES = ("active", "cleared", "oversent")

PROOF_IMAGE_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
MAX_PROOF_BYTES = 5 * 1024 * 1024
MAX_PROOF_LINK_LEN = 2000


class CashoutRecordNotActive(ValueError):
    """Original amount may only change while the cashout is active."""


def _gg_player_id_from_title(group_title: str) -> Optional[str]:
    parsed = parse_tracking_title(group_title)
    if not parsed:
        return None
    return parsed[1]


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _payment_to_dict(payment: StaffCashoutPayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "cashout_record_id": payment.cashout_record_id,
        "payment_method_id": payment.payment_method_id,
        "payment_sub_option_id": payment.payment_sub_option_id,
        "method_display_name": payment.method_display_name,
        "payout_details": payment.payout_details,
        "amount": payment.amount,
        "sort_order": payment.sort_order,
        "created_at": payment.created_at,
    }


def _send_to_dict(row: StaffCashoutMoneySend) -> dict[str, Any]:
    return {
        "id": row.id,
        "cashout_record_id": row.cashout_record_id,
        "sender_name": row.sender_name,
        "amount": row.amount,
        "payment_method_id": row.payment_method_id,
        "payment_sub_option_id": row.payment_sub_option_id,
        "method_display_name": row.method_display_name,
        "notify_player": bool(row.notify_player),
        "notify_status": row.notify_status,
        "notify_error": row.notify_error,
        "notified_at": row.notified_at,
        "proof_link": row.proof_link,
        "has_proof": bool(row.has_proof),
        "created_at": row.created_at,
    }


def record_chat_connected(club_id: Any, chat_id: Any) -> bool:
    """True when the club MTProto account can post in this record's group chat."""
    if chat_id is None or club_id is None:
        return False
    return get_club_gc_config_by_link_club_id(int(club_id)) is not None


def compute_ledger(
    tracks_money_sent: bool, original: Any, sends: list[dict[str, Any]]
) -> dict[str, Any]:
    original_amt = _as_decimal(original)
    if not tracks_money_sent:
        return {
            "tracks_money_sent": False,
            "sent": Decimal("0"),
            "remaining": Decimal("0"),
            "status": "cleared",
        }
    sent = sum((_as_decimal(s.get("amount")) for s in sends), Decimal("0"))
    remaining = original_amt - sent
    if sent < original_amt:
        status = "active"
    elif sent == original_amt:
        status = "cleared"
    else:
        status = "oversent"
    return {
        "tracks_money_sent": True,
        "sent": sent,
        "remaining": remaining,
        "status": status,
    }


def _record_to_dict(record: StaffCashoutRecord) -> dict[str, Any]:
    payments = [
        _payment_to_dict(p) for p in sorted(record.payments, key=lambda p: p.sort_order)
    ]
    sends = [
        _send_to_dict(s)
        for s in sorted(record.money_sends, key=lambda r: r.created_at or datetime.min)
    ]
    tracks = bool(record.tracks_money_sent)
    ledger = compute_ledger(tracks, record.amount, sends)
    return {
        "id": record.id,
        "cashier_job_id": record.cashier_job_id,
        "club_id": record.club_id,
        "chat_id": record.chat_id,
        "group_title": record.group_title,
        "gg_player_id": record.gg_player_id,
        "amount": record.amount,
        "recorded_by_telegram_user_id": record.recorded_by_telegram_user_id,
        "trigger": record.trigger,
        "tracks_money_sent": tracks,
        "sending": bool(getattr(record, "sending", False)),
        "do_not_send": bool(getattr(record, "do_not_send", False)),
        "audited": bool(getattr(record, "audited", False)),
        "chat_connected": record_chat_connected(record.club_id, record.chat_id),
        "owed_clear_status": record.owed_clear_status,
        "owed_clear_error": record.owed_clear_error,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "payments": payments,
        "sends": sends,
        **ledger,
    }


def _record_dict_reloaded(session, record: StaffCashoutRecord) -> dict[str, Any]:
    """Flush then reload collections so add/delete show up in the API response."""
    session.flush()
    session.expire(record, ["payments", "money_sends"])
    return _record_to_dict(record)


def _sync_owed_clear(session, record: StaffCashoutRecord) -> None:
    """Queue the owed-pin clear once money sent covers the cashout.

    Only a cashout that reaches $0 remaining is queued (partial sends never touch the
    pin). A still-pending clear is withdrawn if an edit/delete reopens the balance; a
    finished one is left alone.
    """
    session.flush()
    session.expire(record, ["money_sends"])
    sends = [{"amount": s.amount} for s in record.money_sends]
    ledger = compute_ledger(bool(record.tracks_money_sent), record.amount, sends)
    covered = bool(record.tracks_money_sent) and ledger["status"] in (
        "cleared",
        "oversent",
    )
    if covered and record.chat_id is not None and record.owed_clear_status is None:
        record.owed_clear_status = "pending"
        record.owed_clear_error = None
    elif not covered and record.owed_clear_status == "pending":
        record.owed_clear_status = None


def _is_crypto_method(method_id: Optional[int], display: str) -> bool:
    if method_id is not None:
        method = get_method_by_id(method_id) or {}
        slug = (method.get("slug") or "").strip().lower()
        if slug:
            return slug.startswith("crypto")
    return (display or "").strip().lower().startswith("crypto")


def _validate_proof(
    *, is_crypto: bool, proof_link: Any, proof: Optional[dict[str, Any]]
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    link = (proof_link or "").strip() or None
    if is_crypto:
        if proof:
            raise ValueError("Crypto sends take a transaction link, not a screenshot")
        if link is not None:
            if not link.lower().startswith(("http://", "https://")):
                raise ValueError("Transaction link must start with http:// or https://")
            if len(link) > MAX_PROOF_LINK_LEN:
                raise ValueError("Transaction link is too long")
        return link, None
    if link is not None:
        raise ValueError("Only crypto sends take a transaction link")
    if not proof:
        return None, None
    content = proof.get("content") or b""
    content_type = (proof.get("content_type") or "").strip().lower()
    if not content:
        raise ValueError("Screenshot is empty")
    if content_type not in PROOF_IMAGE_CONTENT_TYPES:
        raise ValueError("Screenshot must be a JPEG, PNG, WebP or GIF image")
    if len(content) > MAX_PROOF_BYTES:
        raise ValueError("Screenshot must be 5 MB or smaller")
    filename = (proof.get("filename") or "screenshot").strip()[:255] or "screenshot"
    return None, {
        "content": content,
        "content_type": content_type,
        "filename": filename,
    }


def _validate_method_choice(
    *,
    payment_method_id: Any,
    payment_sub_option_id: Any,
    method_display_name: Any,
    payout_details: Any = None,
    require_payout_details: bool,
) -> tuple[Optional[int], Optional[int], str, Optional[str]]:
    method_id = int(payment_method_id) if payment_method_id is not None else None
    sub_id = int(payment_sub_option_id) if payment_sub_option_id is not None else None
    display = (method_display_name or "").strip()
    details = (payout_details or "").strip() if payout_details is not None else ""

    if method_id is None:
        if not display:
            raise ValueError("Custom method name is required")
        if require_payout_details:
            pass
        return None, None, display, (details or None)

    method = get_method_by_id(method_id)
    if not method:
        raise ValueError("Payment method not found")
    display = (method.get("name") or display or "").strip()
    if not display:
        raise ValueError("Method name is required")
    if method.get("has_sub_options"):
        if sub_id is None:
            raise ValueError("Sub-option is required for this method")
        sub = get_sub_option_by_id(sub_id)
        if not sub:
            raise ValueError("Sub-option not found")
        sub_name = (sub.get("name") or "").strip()
        if sub_name:
            display = f"{display} / {sub_name}"
    else:
        sub_id = None
    if require_payout_details and not details:
        raise ValueError("Payout details are required for this method")
    return method_id, sub_id, display, (details or None)


def get_staff_cashout_record(record_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        return _record_to_dict(record)


def get_staff_cashout_record_by_job_id(cashier_job_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.cashier_job_id == int(cashier_job_id))
            .first()
        )
        if not record:
            return None
        return _record_to_dict(record)


def create_staff_cashout_record_from_job(job: dict[str, Any]) -> Optional[int]:
    """Create order + first destination. New rows track money sent (start at $0)."""
    job_id = job.get("id")
    if job_id is None:
        return None

    with get_db() as session:
        existing = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.cashier_job_id == int(job_id))
            .first()
        )
        if existing:
            logger.info(
                "staff_cashout_record already exists job_id=%s record_id=%s",
                job_id,
                existing.id,
            )
            return existing.id

        group_title = job.get("group_title") or ""
        amount = job.get("amount")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount or 0))

        record = StaffCashoutRecord(
            cashier_job_id=int(job_id),
            club_id=int(job["club_id"]),
            chat_id=int(job["chat_id"]),
            group_title=group_title,
            gg_player_id=_gg_player_id_from_title(group_title),
            amount=amount,
            recorded_by_telegram_user_id=int(job["initiated_by"]),
            trigger=str(job.get("trigger") or "group_cash"),
            tracks_money_sent=True,
        )
        session.add(record)
        session.flush()

        payment = StaffCashoutPayment(
            cashout_record_id=record.id,
            payment_method_id=job.get("payment_method_id"),
            payment_sub_option_id=job.get("payment_sub_option_id"),
            method_display_name=job.get("method_display_name"),
            payout_details=job.get("payout_details"),
            amount=None,
            sort_order=0,
        )
        session.add(payment)
        session.flush()
        record_id = int(record.id)
        logger.info(
            "staff_cashout_record created job_id=%s record_id=%s",
            job_id,
            record_id,
        )

    try:
        from bot.services.staff_cashout_pushover import (
            SOURCE_CREATE,
            notify_cashout_pushover_sync,
        )

        notify_cashout_pushover_sync(
            record_id,
            source=SOURCE_CREATE,
        )
    except Exception:
        logger.exception(
            "staff_cashout_record: create pushover failed record_id=%s",
            record_id,
        )
    return record_id


_BOUND_PAYMENT_MODELS = (
    VenmoPayment,
    CashAppPayment,
    PayPalPayment,
    ZellePayment,
    CryptoPayment,
)


def chat_has_bound_payment(chat_id: int) -> bool:
    """True when this group has a non-test bound payment on any method.

    Methods: Venmo, Cash App, PayPal, Zelle, crypto, and a completed Stripe
    checkout. Test ingest rows do not count.
    """
    cid = int(chat_id)
    with get_db() as session:
        for model in _BOUND_PAYMENT_MODELS:
            row = (
                session.query(model.id)
                .filter(
                    model.telegram_chat_id == cid,
                    model.is_test.is_(False),
                )
                .first()
            )
            if row is not None:
                return True
        stripe = (
            session.query(StripeCheckoutSession.id)
            .filter(
                StripeCheckoutSession.telegram_chat_id == cid,
                StripeCheckoutSession.status == "complete",
            )
            .first()
        )
        return stripe is not None


def apply_low_deposit_cashout_hold(record_id: int) -> Optional[dict[str, Any]]:
    """Park a cashout with do_not_send when the group has no bound payment.

    Returns a Slack payload only when newly parked. Skips missing chat_id and
    records already marked do_not_send. Fail-closed on lookup errors.
    """
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        if record.chat_id is None:
            return None
        if bool(getattr(record, "do_not_send", False)):
            return None

        reason: str
        try:
            has_bound_payment = chat_has_bound_payment(int(record.chat_id))
        except Exception:
            logger.exception(
                "low_deposit_hold: bound-payment lookup failed record_id=%s chat_id=%s",
                record_id,
                record.chat_id,
            )
            reason = "lookup_failed"
        else:
            if has_bound_payment:
                return None
            reason = "no_bound_payment"

        record.do_not_send = True
        record.updated_at = datetime.utcnow()
        logger.info(
            "low_deposit_hold: parked record_id=%s chat_id=%s reason=%s",
            record.id,
            record.chat_id,
            reason,
        )
        return {
            "record_id": int(record.id),
            "club_id": int(record.club_id),
            "chat_id": int(record.chat_id),
            "group_title": record.group_title or "",
            "gg_player_id": record.gg_player_id,
            "amount": record.amount,
            "reason": reason,
        }


def create_staff_cashout_record_manual(
    *,
    club_id: int,
    group_title: str,
    amount: Decimal,
    payments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title = (group_title or "").strip()
    if not title:
        raise ValueError("Name is required")
    amt = _as_decimal(amount)
    if amt <= 0:
        raise ValueError("Amount must be greater than zero")
    payment_rows = list(payments or [])
    if not payment_rows:
        raise ValueError("At least one payment destination is required")

    with get_db() as session:
        club = session.get(Club, int(club_id))
        if not club:
            raise ValueError("Club not found")
        record = StaffCashoutRecord(
            cashier_job_id=None,
            club_id=int(club_id),
            chat_id=None,
            group_title=title,
            gg_player_id=_gg_player_id_from_title(title),
            amount=amt,
            recorded_by_telegram_user_id=None,
            trigger="dashboard",
            tracks_money_sent=True,
        )
        session.add(record)
        session.flush()

        for idx, pdata in enumerate(payment_rows):
            method_id, sub_id, display, details = _validate_method_choice(
                payment_method_id=pdata.get("payment_method_id"),
                payment_sub_option_id=pdata.get("payment_sub_option_id"),
                method_display_name=pdata.get("method_display_name"),
                payout_details=pdata.get("payout_details"),
                require_payout_details=pdata.get("payment_method_id") is not None,
            )
            session.add(
                StaffCashoutPayment(
                    cashout_record_id=int(record.id),
                    payment_method_id=method_id,
                    payment_sub_option_id=sub_id,
                    method_display_name=display,
                    payout_details=details,
                    amount=pdata.get("amount"),
                    sort_order=pdata.get("sort_order", idx),
                )
            )

        session.flush()
        record_id = int(record.id)
        logger.info(
            "staff_cashout_record created from dashboard record_id=%s payments=%s",
            record_id,
            len(payment_rows),
        )
        session.expire(record, ["payments", "money_sends"])
        data = _record_to_dict(record)

    try:
        from bot.services.staff_cashout_pushover import (
            SOURCE_CREATE,
            notify_cashout_pushover_sync,
        )

        notify_cashout_pushover_sync(
            record_id,
            source=SOURCE_CREATE,
        )
    except Exception:
        logger.exception(
            "staff_cashout_record: create pushover failed record_id=%s",
            record_id,
        )
    return data


def delete_staff_cashout_record(record_id: int) -> bool:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return False
        session.delete(record)
        session.flush()
        logger.info("staff_cashout_record deleted record_id=%s", record_id)
        return True


def update_staff_cashout_record(
    record_id: int,
    *,
    group_title: Optional[str] = None,
    amount: Optional[Decimal] = None,
    sending: Optional[bool] = None,
    do_not_send: Optional[bool] = None,
    audited: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        current = _record_to_dict(record)
        if amount is not None and current["status"] != "active":
            raise CashoutRecordNotActive(
                "Original amount can only be edited while active"
            )
        if group_title is not None:
            record.group_title = group_title
            record.gg_player_id = _gg_player_id_from_title(group_title)
        if amount is not None:
            record.amount = amount
        if sending is not None:
            record.sending = bool(sending)
        if do_not_send is not None:
            record.do_not_send = bool(do_not_send)
        if audited is not None:
            if bool(audited) and current["status"] != "cleared":
                raise ValueError("Audited can only be set when remaining is zero")
            record.audited = bool(audited)
        record.updated_at = datetime.utcnow()
        if amount is not None:
            _sync_owed_clear(session, record)
        return _record_dict_reloaded(session, record)


def replace_staff_cashout_payments(
    record_id: int, payments: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None

        session.query(StaffCashoutPayment).filter(
            StaffCashoutPayment.cashout_record_id == int(record_id)
        ).delete(synchronize_session=False)

        for idx, pdata in enumerate(payments):
            method_id, sub_id, display, details = _validate_method_choice(
                payment_method_id=pdata.get("payment_method_id"),
                payment_sub_option_id=pdata.get("payment_sub_option_id"),
                method_display_name=pdata.get("method_display_name"),
                payout_details=pdata.get("payout_details"),
                require_payout_details=pdata.get("payment_method_id") is not None,
            )
            session.add(
                StaffCashoutPayment(
                    cashout_record_id=int(record_id),
                    payment_method_id=method_id,
                    payment_sub_option_id=sub_id,
                    method_display_name=display,
                    payout_details=details,
                    amount=pdata.get("amount"),
                    sort_order=pdata.get("sort_order", idx),
                )
            )
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def add_staff_cashout_payment(
    record_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        method_id, sub_id, display, details = _validate_method_choice(
            payment_method_id=pdata.get("payment_method_id"),
            payment_sub_option_id=pdata.get("payment_sub_option_id"),
            method_display_name=pdata.get("method_display_name"),
            payout_details=pdata.get("payout_details"),
            require_payout_details=pdata.get("payment_method_id") is not None,
        )
        max_order = max((p.sort_order for p in record.payments), default=-1)
        session.add(
            StaffCashoutPayment(
                cashout_record_id=int(record_id),
                payment_method_id=method_id,
                payment_sub_option_id=sub_id,
                method_display_name=display,
                payout_details=details,
                amount=pdata.get("amount"),
                sort_order=pdata.get("sort_order", max_order + 1),
            )
        )
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def update_staff_cashout_payment(
    record_id: int, payment_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        payment = session.get(StaffCashoutPayment, int(payment_id))
        if not payment or payment.cashout_record_id != int(record_id):
            return None
        merged = {
            "payment_method_id": payment.payment_method_id,
            "payment_sub_option_id": payment.payment_sub_option_id,
            "method_display_name": payment.method_display_name,
            "payout_details": payment.payout_details,
        }
        for key in merged:
            if key in pdata:
                merged[key] = pdata[key]
        method_id, sub_id, display, details = _validate_method_choice(
            payment_method_id=merged["payment_method_id"],
            payment_sub_option_id=merged["payment_sub_option_id"],
            method_display_name=merged["method_display_name"],
            payout_details=merged["payout_details"],
            require_payout_details=merged["payment_method_id"] is not None,
        )
        payment.payment_method_id = method_id
        payment.payment_sub_option_id = sub_id
        payment.method_display_name = display
        payment.payout_details = details
        if "amount" in pdata:
            payment.amount = pdata["amount"]
        if "sort_order" in pdata and pdata["sort_order"] is not None:
            payment.sort_order = pdata["sort_order"]
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def delete_staff_cashout_payment(
    record_id: int, payment_id: int
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        payment = session.get(StaffCashoutPayment, int(payment_id))
        if not payment or payment.cashout_record_id != int(record_id):
            return None
        session.delete(payment)
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def add_staff_cashout_send(
    record_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        sender = (pdata.get("sender_name") or "").strip()
        if not sender:
            raise ValueError("Name is required")
        amount = _as_decimal(pdata.get("amount"))
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")
        method_id, sub_id, display, _details = _validate_method_choice(
            payment_method_id=pdata.get("payment_method_id"),
            payment_sub_option_id=pdata.get("payment_sub_option_id"),
            method_display_name=pdata.get("method_display_name"),
            payout_details=None,
            require_payout_details=False,
        )
        link, proof = _validate_proof(
            is_crypto=_is_crypto_method(method_id, display),
            proof_link=pdata.get("proof_link"),
            proof=pdata.get("proof"),
        )
        notify = bool(pdata.get("notify_player")) and record_chat_connected(
            record.club_id, record.chat_id
        )
        row = StaffCashoutMoneySend(
            cashout_record_id=int(record_id),
            sender_name=sender,
            amount=amount,
            payment_method_id=method_id,
            payment_sub_option_id=sub_id,
            method_display_name=display,
            notify_player=notify,
            notify_status="pending" if notify else None,
            proof_link=link,
            has_proof=proof is not None,
        )
        if proof is not None:
            row.proof = StaffCashoutSendProof(**proof)
        session.add(row)
        record.updated_at = datetime.utcnow()
        _sync_owed_clear(session, record)
        return _record_dict_reloaded(session, record)


def get_staff_cashout_send_proof(
    record_id: int, send_id: int
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        proof = (
            session.query(StaffCashoutSendProof)
            .join(
                StaffCashoutMoneySend,
                StaffCashoutMoneySend.id == StaffCashoutSendProof.money_send_id,
            )
            .filter(
                StaffCashoutMoneySend.id == int(send_id),
                StaffCashoutMoneySend.cashout_record_id == int(record_id),
            )
            .first()
        )
        if not proof:
            return None
        return {
            "content": bytes(proof.content),
            "content_type": proof.content_type,
            "filename": proof.filename,
        }


def update_staff_cashout_send(
    record_id: int, send_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        row = session.get(StaffCashoutMoneySend, int(send_id))
        if not row or row.cashout_record_id != int(record_id):
            return None
        if "sender_name" in pdata:
            sender = (pdata.get("sender_name") or "").strip()
            if not sender:
                raise ValueError("Name is required")
            row.sender_name = sender
        if "amount" in pdata and pdata["amount"] is not None:
            amount = _as_decimal(pdata.get("amount"))
            if amount <= 0:
                raise ValueError("Amount must be greater than zero")
            row.amount = amount
        method_id = (
            row.payment_method_id
            if "payment_method_id" not in pdata
            else pdata.get("payment_method_id")
        )
        sub_id = (
            row.payment_sub_option_id
            if "payment_sub_option_id" not in pdata
            else pdata.get("payment_sub_option_id")
        )
        display = (
            row.method_display_name
            if "method_display_name" not in pdata
            else pdata.get("method_display_name")
        )
        if any(
            k in pdata
            for k in (
                "payment_method_id",
                "payment_sub_option_id",
                "method_display_name",
            )
        ):
            method_id, sub_id, display, _d = _validate_method_choice(
                payment_method_id=method_id,
                payment_sub_option_id=sub_id,
                method_display_name=display,
                payout_details=None,
                require_payout_details=False,
            )
            row.payment_method_id = method_id
            row.payment_sub_option_id = sub_id
            row.method_display_name = display
        record.updated_at = datetime.utcnow()
        _sync_owed_clear(session, record)
        return _record_dict_reloaded(session, record)


def delete_staff_cashout_send(record_id: int, send_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        row = session.get(StaffCashoutMoneySend, int(send_id))
        if not row or row.cashout_record_id != int(record_id):
            return None
        session.delete(row)
        record.updated_at = datetime.utcnow()
        _sync_owed_clear(session, record)
        return _record_dict_reloaded(session, record)


def _matches_search(out: dict[str, Any], needle: str) -> bool:
    values = [out.get(k) for k in ("group_title", "gg_player_id", "club_name")]
    values.extend(payment.get("payout_details") for payment in out.get("payments", []))
    blob = " ".join(str(value or "") for value in values).lower()
    return needle.lower() in blob


def list_staff_cashout_records(
    *,
    club_id: Optional[int] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    audited: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if status is not None and status not in STATUSES:
        raise ValueError("status must be active, cleared, oversent, or do_not_send")
    cap = max(1, min(int(limit), 500))
    skip = max(0, int(offset))
    needle = None
    if q:
        cleaned = str(q).replace("%", "").replace("_", "").strip()
        needle = cleaned or None
    with get_db() as session:
        club_names = {
            int(row.id): str(row.name)
            for row in session.query(Club.id, Club.name).all()
        }
        query = (
            session.query(StaffCashoutRecord)
            .outerjoin(Club, Club.id == StaffCashoutRecord.club_id)
            .order_by(StaffCashoutRecord.created_at.desc())
        )
        if club_id is not None:
            query = query.filter(StaffCashoutRecord.club_id == int(club_id))
        if status == "do_not_send":
            query = query.filter(StaffCashoutRecord.do_not_send.is_(True))
        elif status in LEDGER_STATUSES:
            query = query.filter(StaffCashoutRecord.do_not_send.is_(False))
        if audited is not None:
            query = query.filter(StaffCashoutRecord.audited.is_(bool(audited)))
        if needle:
            like = f"%{needle}%"
            query = query.filter(
                or_(
                    StaffCashoutRecord.group_title.ilike(like),
                    StaffCashoutRecord.gg_player_id.ilike(like),
                    Club.name.ilike(like),
                    exists().where(
                        StaffCashoutPayment.cashout_record_id == StaffCashoutRecord.id,
                        StaffCashoutPayment.payout_details.ilike(like),
                    ),
                )
            )
        rows = query.all()
        filtered: list[dict[str, Any]] = []
        for record in rows:
            out = _record_to_dict(record)
            out["club_name"] = club_names.get(int(record.club_id))
            if status == "do_not_send":
                if not out.get("do_not_send"):
                    continue
            elif status in LEDGER_STATUSES:
                if out.get("do_not_send") or out["status"] != status:
                    continue
            if needle and not _matches_search(out, needle):
                continue
            filtered.append(out)
        total = len(filtered)
        return filtered[skip : skip + cap], total


def _money_send_ledger_dict(
    send: StaffCashoutMoneySend,
    record: StaffCashoutRecord,
    club_names: dict[int, str],
) -> dict[str, Any]:
    club_id = int(record.club_id)
    return {
        "id": send.id,
        "cashout_record_id": int(send.cashout_record_id),
        "sender_name": send.sender_name,
        "amount": send.amount,
        "payment_method_id": send.payment_method_id,
        "payment_sub_option_id": send.payment_sub_option_id,
        "method_display_name": send.method_display_name,
        "created_at": send.created_at,
        "club_id": club_id,
        "club_name": club_names.get(club_id),
        "group_title": record.group_title,
        "gg_player_id": record.gg_player_id,
    }


def _filter_money_send_query(
    session,
    *,
    club_id: Optional[int] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    method_display_name: Optional[str] = None,
    q: Optional[str] = None,
):
    query = session.query(StaffCashoutMoneySend, StaffCashoutRecord).join(
        StaffCashoutRecord,
        StaffCashoutMoneySend.cashout_record_id == StaffCashoutRecord.id,
    )
    if club_id is not None:
        query = query.filter(StaffCashoutRecord.club_id == int(club_id))
    if from_dt is not None:
        query = query.filter(StaffCashoutMoneySend.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(StaffCashoutMoneySend.created_at <= to_dt)
    if method_display_name is not None:
        method = str(method_display_name).strip()
        if method:
            query = query.filter(StaffCashoutMoneySend.method_display_name == method)
    if q:
        cleaned = str(q).replace("%", "").replace("_", "").strip()
        if cleaned:
            like = f"%{cleaned}%"
            query = query.filter(
                or_(
                    StaffCashoutMoneySend.sender_name.ilike(like),
                    StaffCashoutRecord.group_title.ilike(like),
                    StaffCashoutRecord.gg_player_id.ilike(like),
                )
            )
    return query


def list_staff_cashout_money_sends(
    *,
    club_id: Optional[int] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    method_display_name: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Flat ledger of money-sent rows across cashouts (newest first)."""
    cap = max(1, min(int(limit), 10000))
    skip = max(0, int(offset))
    with get_db() as session:
        club_names = {
            int(row.id): str(row.name)
            for row in session.query(Club.id, Club.name).all()
        }
        query = _filter_money_send_query(
            session,
            club_id=club_id,
            from_dt=from_dt,
            to_dt=to_dt,
            method_display_name=method_display_name,
            q=q,
        )
        total = query.count()
        ordered = query.order_by(
            StaffCashoutMoneySend.created_at.desc(),
            StaffCashoutMoneySend.id.desc(),
        )
        results = []
        for send, record in ordered.offset(skip).limit(cap).all():
            results.append(_money_send_ledger_dict(send, record, club_names))
        return results, total


def list_money_send_method_names(
    *,
    club_id: Optional[int] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> list[str]:
    """Distinct method_display_name values in the club/date window (for filter dropdown)."""
    with get_db() as session:
        query = session.query(StaffCashoutMoneySend.method_display_name).join(
            StaffCashoutRecord,
            StaffCashoutMoneySend.cashout_record_id == StaffCashoutRecord.id,
        )
        if club_id is not None:
            query = query.filter(StaffCashoutRecord.club_id == int(club_id))
        if from_dt is not None:
            query = query.filter(StaffCashoutMoneySend.created_at >= from_dt)
        if to_dt is not None:
            query = query.filter(StaffCashoutMoneySend.created_at <= to_dt)
        rows = (
            query.distinct()
            .order_by(StaffCashoutMoneySend.method_display_name.asc())
            .all()
        )
        names: list[str] = []
        for (name,) in rows:
            text = (name or "").strip()
            if text and text not in names:
                names.append(text)
        return names
