"""Trace ClubGG RPA calls and lock a staff /add or /cash before chips move."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from db.connection import get_db
from db.models import ClubggRpaEvent

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 10

OUTCOME_PROCEED = "proceed"
OUTCOME_STALE = "stale"
OUTCOME_OWNED = "owned"
OUTCOME_PERSIST_FAILED = "persist_failed"

PATH_TELETHON = "telethon"
PATH_BOT_FALLBACK = "bot_fallback"
PATH_BOT_DIRECT = "bot_direct"

OP_DEPOSIT = "deposit"
OP_CLAIM = "claim"
OP_RAKE = "rake"

SOURCE_STAFF_ADD = "staff_add"
SOURCE_STAFF_BONUS = "staff_bonus"
SOURCE_STAFF_CASH = "staff_cash"

# A chip-add still waiting after this is discarded and not sent.
CHIP_ADD_TTL = timedelta(seconds=30)
# Let the process that saved the row send it before the worker picks it up.
CHIP_ADD_SWEEP_GRACE = timedelta(seconds=5)

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_EXPIRED = "expired"
CHIP_ADD_TTL_DETAIL = "chip-add ttl elapsed"


def message_sent_at(value: datetime | None) -> datetime | None:
    """Telegram message.date as UTC, or None when the metadata has no send time."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_command_stale(sent_at: datetime | None, *, now: datetime | None = None) -> bool:
    """True when the command is missing a send time or is older than 10 seconds."""
    sent = message_sent_at(sent_at)
    if sent is None:
        return True
    current = message_sent_at(now) or datetime.now(timezone.utc)
    return (current - sent).total_seconds() > STALE_AFTER_SECONDS


def infer_source(request_id: str) -> str:
    rid = request_id or ""
    if rid.startswith("payment-"):
        return "payment_auto_deposit"
    if rid.startswith("transfer-"):
        return "transfer"
    if rid.startswith("earlyrb-"):
        return "early_rakeback"
    if rid.startswith("auto-cashout-"):
        return "auto_cashout"
    if rid.startswith("cash-"):
        return SOURCE_STAFF_CASH
    if rid.startswith("tg-"):
        return SOURCE_STAFF_ADD
    return "clubgg"


def cash_command_request_id(chat_id: int, message_id: int) -> str:
    return f"cash-cmd-{int(chat_id)}-{int(message_id)}"


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def claim_command(
    *,
    request_id: str,
    operation: str,
    source: str,
    telegram_chat_id: int,
    message_id: int,
    club_id: int | None,
    group_title: str | None,
    amount: Decimal | None,
    bonus: Decimal | None,
    message_sent_at_value: datetime | None,
    path: str,
    now: datetime | None = None,
) -> str:
    """Insert the command lock. The first insert owns delete, escalate, and ClubGG.

    Returns proceed, stale, owned, or persist_failed.
    """
    sent = message_sent_at(message_sent_at_value)
    stale = is_command_stale(sent, now=now)
    try:
        with get_db() as session:
            session.add(
                ClubggRpaEvent(
                    request_id=request_id,
                    operation=operation,
                    source=source,
                    status="stale" if stale else "started",
                    command_lock=True,
                    telegram_chat_id=int(telegram_chat_id),
                    message_id=int(message_id),
                    club_id=int(club_id) if club_id is not None else None,
                    group_title=_clip(group_title, 255),
                    amount=amount,
                    bonus=bonus,
                    message_sent_at=sent,
                    started_at=message_sent_at(now) or datetime.now(timezone.utc),
                    path=path,
                )
            )
            session.flush()
    except IntegrityError:
        logger.info(
            "clubgg_rpa: command already owned request_id=%s chat_id=%s message_id=%s",
            request_id,
            telegram_chat_id,
            message_id,
        )
        return OUTCOME_OWNED
    except Exception:
        logger.exception(
            "clubgg_rpa: command lock failed request_id=%s chat_id=%s",
            request_id,
            telegram_chat_id,
        )
        return OUTCOME_PERSIST_FAILED
    if stale:
        logger.info(
            "clubgg_rpa: stale command request_id=%s chat_id=%s message_id=%s path=%s",
            request_id,
            telegram_chat_id,
            message_id,
            path,
        )
        return OUTCOME_STALE
    return OUTCOME_PROCEED


def record_clubgg_result(
    *,
    request_id: str,
    operation: str,
    ok: bool,
    rpa_status: str | None,
    club_id: int | None = None,
    telegram_chat_id: int | None = None,
    amount: Decimal | None = None,
    group_title: str | None = None,
    detail: str | None = None,
) -> None:
    """Upsert the ClubGG result. Never raises. Does not block the RPA call."""
    if (rpa_status or "") == "request_claim_failed":
        return
    final = "completed" if ok else "failed"
    try:
        with get_db() as session:
            row = (
                session.query(ClubggRpaEvent)
                .filter_by(request_id=request_id)
                .one_or_none()
            )
            if row is not None and row.status == "stale":
                return
            if row is None:
                session.add(
                    ClubggRpaEvent(
                        request_id=request_id,
                        operation=operation,
                        source=infer_source(request_id),
                        status=final,
                        command_lock=False,
                        telegram_chat_id=(
                            int(telegram_chat_id)
                            if telegram_chat_id is not None
                            else None
                        ),
                        club_id=int(club_id) if club_id is not None else None,
                        group_title=_clip(group_title, 255),
                        amount=amount,
                        started_at=datetime.now(timezone.utc),
                        rpa_status=_clip(rpa_status, 32),
                        detail=_clip(detail, 500),
                    )
                )
                return
            row.rpa_status = _clip(rpa_status, 32)
            row.status = final
            if detail:
                row.detail = _clip(detail, 500)
            if row.club_id is None and club_id is not None:
                row.club_id = int(club_id)
            if row.telegram_chat_id is None and telegram_chat_id is not None:
                row.telegram_chat_id = int(telegram_chat_id)
            if row.group_title is None and group_title:
                row.group_title = _clip(group_title, 255)
            if row.amount is None and amount is not None:
                row.amount = amount
    except Exception:
        logger.exception("clubgg_rpa: result record failed request_id=%s", request_id)


def _aware(value: datetime | None) -> datetime | None:
    return message_sent_at(value)


def chip_add_expired(expires_at: datetime | None, now: datetime) -> bool:
    expires = _aware(expires_at)
    if expires is None:
        return False
    return expires <= _aware(now)


def pending_chip_add_due(
    *,
    started_at: datetime | None,
    expires_at: datetime | None,
    now: datetime,
    grace: timedelta = CHIP_ADD_SWEEP_GRACE,
) -> bool:
    """True when a pending row is old enough for the worker and still inside its ttl."""
    if chip_add_expired(expires_at, now):
        return False
    started = _aware(started_at)
    current = _aware(now)
    if started is None or current is None:
        return False
    return started <= current - grace


def enqueue_pending_chip_add(
    *,
    request_id: str,
    club_id: int | None,
    telegram_chat_id: int | None,
    amount: Decimal | None,
    group_title: str | None,
    now: datetime | None = None,
) -> None:
    """Save a chip-add before the ClubGG call. Never raises. Existing rows stay put."""
    current = _aware(now) or datetime.now(timezone.utc)
    try:
        with get_db() as session:
            exists = (
                session.query(ClubggRpaEvent.id)
                .filter_by(request_id=request_id)
                .one_or_none()
            )
            if exists is not None:
                return
            session.add(
                ClubggRpaEvent(
                    request_id=request_id,
                    operation=OP_DEPOSIT,
                    source=infer_source(request_id),
                    status=STATUS_PENDING,
                    command_lock=False,
                    telegram_chat_id=(
                        int(telegram_chat_id) if telegram_chat_id is not None else None
                    ),
                    club_id=int(club_id) if club_id is not None else None,
                    group_title=_clip(group_title, 255),
                    amount=amount,
                    started_at=current,
                    expires_at=current + CHIP_ADD_TTL,
                )
            )
    except IntegrityError:
        return
    except Exception:
        logger.exception("clubgg_rpa: enqueue failed request_id=%s", request_id)


def claim_pending_chip_add(request_id: str, *, now: datetime | None = None) -> str:
    """Take a pending chip-add. claimed, owned, expired, completed, failed, or missing.

    missing means the row could not be read; the caller may still send.
    """
    current = _aware(now) or datetime.now(timezone.utc)
    try:
        with get_db() as session:
            claimed = session.execute(
                update(ClubggRpaEvent)
                .where(
                    ClubggRpaEvent.request_id == request_id,
                    ClubggRpaEvent.status == STATUS_PENDING,
                    or_(
                        ClubggRpaEvent.expires_at.is_(None),
                        ClubggRpaEvent.expires_at > current,
                    ),
                )
                .values(status=STATUS_SENDING)
            )
            if claimed.rowcount == 1:
                return "claimed"
            row = (
                session.query(ClubggRpaEvent)
                .filter_by(request_id=request_id)
                .one_or_none()
            )
            if row is None:
                return "missing"
            if row.status == "completed":
                return "completed"
            if row.status == "failed":
                return "failed"
            if row.status == STATUS_EXPIRED or chip_add_expired(
                row.expires_at, current
            ):
                return "expired"
            return "owned"
    except Exception:
        logger.exception("clubgg_rpa: claim failed request_id=%s", request_id)
        return "missing"


def fail_pending_chip_add(request_id: str, *, detail: str) -> None:
    """Close a chip-add that will not be sent. Leaves a row another runner already took."""
    try:
        with get_db() as session:
            session.execute(
                update(ClubggRpaEvent)
                .where(
                    ClubggRpaEvent.request_id == request_id,
                    ClubggRpaEvent.status == STATUS_PENDING,
                )
                .values(
                    status="failed",
                    rpa_status=_clip(detail, 32),
                    detail=_clip(detail, 500),
                )
            )
    except Exception:
        logger.exception("clubgg_rpa: fail pending failed request_id=%s", request_id)


def _chip_add_snapshot(row: ClubggRpaEvent) -> dict:
    return {
        "request_id": row.request_id,
        "club_id": row.club_id,
        "telegram_chat_id": row.telegram_chat_id,
        "amount": row.amount,
        "group_title": row.group_title,
        "source": row.source,
        "started_at": row.started_at,
        "expires_at": row.expires_at,
    }


def due_pending_chip_adds(
    now: datetime | None = None,
    *,
    grace: timedelta = CHIP_ADD_SWEEP_GRACE,
) -> list[dict]:
    """Pending chip-adds the saver did not claim within the grace period."""
    current = _aware(now) or datetime.now(timezone.utc)
    cutoff = current - grace
    try:
        with get_db() as session:
            rows = (
                session.query(ClubggRpaEvent)
                .filter(
                    ClubggRpaEvent.operation == OP_DEPOSIT,
                    ClubggRpaEvent.command_lock.is_(False),
                    ClubggRpaEvent.status == STATUS_PENDING,
                    ClubggRpaEvent.expires_at.is_not(None),
                    ClubggRpaEvent.expires_at > current,
                    ClubggRpaEvent.started_at <= cutoff,
                )
                .all()
            )
            return [_chip_add_snapshot(row) for row in rows]
    except Exception:
        logger.exception("clubgg_rpa: due pending lookup failed")
        return []


def expire_elapsed_chip_adds(now: datetime | None = None) -> list[dict]:
    """Mark chip-adds that are still waiting after the ttl. A send already started is left alone."""
    current = _aware(now) or datetime.now(timezone.utc)
    try:
        with get_db() as session:
            rows = (
                session.query(ClubggRpaEvent)
                .filter(
                    ClubggRpaEvent.operation == OP_DEPOSIT,
                    ClubggRpaEvent.command_lock.is_(False),
                    ClubggRpaEvent.status == STATUS_PENDING,
                    ClubggRpaEvent.expires_at.is_not(None),
                    ClubggRpaEvent.expires_at <= current,
                )
                .all()
            )
            expired = []
            for row in rows:
                row.status = STATUS_EXPIRED
                row.rpa_status = STATUS_EXPIRED
                row.detail = CHIP_ADD_TTL_DETAIL
                expired.append(_chip_add_snapshot(row))
            return expired
    except Exception:
        logger.exception("clubgg_rpa: expire lookup failed")
        return []
