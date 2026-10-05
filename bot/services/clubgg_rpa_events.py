"""Trace ClubGG RPA calls and lock a staff /add or /cash before chips move."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

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
