"""Durable 10-minute expiry for union manual-deposit ack and instruction messages."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker
from bot.services.union_deposit_messages import (
    UNION_ACK_EXPIRED_TEXT,
    UNION_INSTRUCTION_EXPIRED_TEXT,
)
from db.connection import get_db
from db.models import ManualDepositRequest

logger = logging.getLogger(__name__)

UNION_INSTRUCTION_EXPIRY_SECONDS = 600  # 10 minutes
UNION_INSTRUCTION_EXPIRY_SECONDS_TEST = 30
_UNION_DEPOSIT_EXPIRY_SWEEP_JOB = "union_deposit_expiry_sweep"
_UNION_DEPOSIT_EXPIRY_SWEEP_INTERVAL = 60

_union_expiry_app: Any | None = None


class UnionAckExpiryTarget(NamedTuple):
    telegram_chat_id: int
    ack_telegram_message_id: int


class UnionInstructionExpiryTarget(NamedTuple):
    telegram_chat_id: int
    instruction_telegram_message_ids: list[int]


def union_instruction_expiry_seconds() -> int:
    if is_test_bot_worker():
        return UNION_INSTRUCTION_EXPIRY_SECONDS_TEST
    return UNION_INSTRUCTION_EXPIRY_SECONDS


def register_union_instruction_expiry_runtime(app: Any) -> None:
    global _union_expiry_app
    _union_expiry_app = app
    jq = getattr(app, "job_queue", None)
    try:
        restore_union_deposit_expiries(jq)
    except Exception:
        logger.warning(
            "union deposit expiry: restore pending jobs failed", exc_info=True
        )
    if jq is not None:
        try:
            for job in jq.get_jobs_by_name(_UNION_DEPOSIT_EXPIRY_SWEEP_JOB):
                job.schedule_removal()
            jq.run_repeating(
                _union_deposit_expiry_sweep_callback,
                interval=_UNION_DEPOSIT_EXPIRY_SWEEP_INTERVAL,
                first=_UNION_DEPOSIT_EXPIRY_SWEEP_INTERVAL,
                name=_UNION_DEPOSIT_EXPIRY_SWEEP_JOB,
            )
        except Exception:
            logger.warning(
                "union deposit expiry: sweep job setup failed", exc_info=True
            )


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    if _union_expiry_app is not None:
        return getattr(_union_expiry_app, "job_queue", None)
    return None


def _instruction_expiry_job_name(request_id: int) -> str:
    return f"union_instruction_expire_{int(request_id)}"


def _ack_expiry_job_name(request_id: int) -> str:
    return f"union_ack_expire_{int(request_id)}"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def expires_at_from_now(*, now: datetime | None = None) -> datetime:
    base = _as_utc(now) or datetime.now(timezone.utc)
    return base + timedelta(seconds=union_instruction_expiry_seconds())


def instruction_expires_at_from_now(*, now: datetime | None = None) -> datetime:
    return expires_at_from_now(now=now)


def list_pending_union_instruction_expiries() -> list[tuple[int, datetime]]:
    """Return (request_id, expires_at) for worker restore."""
    out: list[tuple[int, datetime]] = []
    with get_db() as session:
        rows = (
            session.query(
                ManualDepositRequest.id,
                ManualDepositRequest.instruction_expires_at,
            )
            .filter(ManualDepositRequest.instruction_expires_at.isnot(None))
            .filter(ManualDepositRequest.instruction_expired_at.is_(None))
            .filter(ManualDepositRequest.trade_record_checked.is_(False))
            .all()
        )
        for request_id, expires_at in rows:
            exp = _as_utc(expires_at)
            if exp is not None:
                out.append((int(request_id), exp))
    return out


def list_pending_union_ack_expiries() -> list[tuple[int, datetime]]:
    """Return (request_id, ack_expires_at) for worker restore."""
    out: list[tuple[int, datetime]] = []
    with get_db() as session:
        rows = (
            session.query(
                ManualDepositRequest.id,
                ManualDepositRequest.ack_expires_at,
            )
            .filter(ManualDepositRequest.ack_expires_at.isnot(None))
            .filter(ManualDepositRequest.ack_expired_at.is_(None))
            .filter(ManualDepositRequest.acknowledged_at.is_(None))
            .filter(ManualDepositRequest.trade_record_checked.is_(False))
            .all()
        )
        for request_id, expires_at in rows:
            exp = _as_utc(expires_at)
            if exp is not None:
                out.append((int(request_id), exp))
    return out


def _cancel_jobs_by_name(job_queue: Any | None, name: str) -> None:
    if job_queue is None:
        return
    try:
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        logger.debug("union deposit expiry: cancel job failed name=%s", name, exc_info=True)


def cancel_union_ack_expiry(
    request_id: int,
    *,
    job_queue: Any | None = None,
    session: Any | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    _cancel_jobs_by_name(jq, _ack_expiry_job_name(int(request_id)))

    def _clear_ack_expiry(db_session) -> None:
        row = db_session.get(ManualDepositRequest, int(request_id))
        if row is None:
            return
        row.ack_expires_at = None
        db_session.flush()

    if session is not None:
        _clear_ack_expiry(session)
        return

    with get_db() as db_session:
        _clear_ack_expiry(db_session)


def cancel_union_instruction_expiry(
    request_id: int,
    *,
    job_queue: Any | None = None,
    session: Any | None = None,
) -> None:
    """Stop pending instruction and ack expiries (e.g. ops marked trade record checked)."""
    jq = _resolve_job_queue(job_queue)
    _cancel_jobs_by_name(jq, _instruction_expiry_job_name(int(request_id)))
    _cancel_jobs_by_name(jq, _ack_expiry_job_name(int(request_id)))

    def _clear_instruction_expiry(db_session) -> None:
        row = db_session.get(ManualDepositRequest, int(request_id))
        if row is None:
            return
        row.instruction_expires_at = None
        row.ack_expires_at = None
        db_session.flush()

    if session is not None:
        _clear_instruction_expiry(session)
        return

    with get_db() as db_session:
        _clear_instruction_expiry(db_session)


async def _edit_message_text(
    bot: Any,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    log_label: str,
) -> None:
    for parse_mode in (None,):
        try:
            kwargs: dict[str, Any] = {
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "text": text,
            }
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode
            await bot.edit_message_text(**kwargs)
            return
        except Exception as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                return
            logger.warning(
                "%s: edit failed chat_id=%s message_id=%s parse_mode=%r: %s",
                log_label,
                chat_id,
                message_id,
                parse_mode,
                exc,
                exc_info=True,
            )


async def _edit_instruction_messages(
    bot: Any,
    *,
    chat_id: int,
    message_ids: list[int],
) -> None:
    for message_id in message_ids:
        await _edit_message_text(
            bot,
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=UNION_INSTRUCTION_EXPIRED_TEXT,
            log_label="union instruction expiry",
        )


def mark_union_ack_expired(request_id: int) -> UnionAckExpiryTarget | None:
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = (
            session.query(ManualDepositRequest)
            .filter(ManualDepositRequest.id == int(request_id))
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return None
        if row.ack_expired_at is not None:
            return None
        if row.acknowledged_at is not None:
            return None
        if bool(row.trade_record_checked):
            row.ack_expires_at = None
            session.flush()
            return None
        if row.ack_expires_at is None:
            return None
        row.ack_expired_at = now
        row.ack_expires_at = None
        session.flush()
        session.refresh(row)
        ack_message_id = row.ack_telegram_message_id
        if ack_message_id is None:
            return None
        return UnionAckExpiryTarget(
            telegram_chat_id=int(row.telegram_chat_id),
            ack_telegram_message_id=int(ack_message_id),
        )


async def expire_union_ack_now(bot: Any, request_id: int) -> bool:
    target = mark_union_ack_expired(int(request_id))
    if target is None:
        return False
    await _edit_message_text(
        bot,
        chat_id=target.telegram_chat_id,
        message_id=target.ack_telegram_message_id,
        text=UNION_ACK_EXPIRED_TEXT,
        log_label="union ack expiry",
    )
    logger.info(
        "union ack expiry: applied request_id=%s chat_id=%s message_id=%s",
        request_id,
        target.telegram_chat_id,
        target.ack_telegram_message_id,
    )
    return True


async def _union_ack_expiry_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None or not job.data:
        return
    request_id = job.data.get("request_id")
    if request_id is None:
        return
    await expire_union_ack_now(context.bot, int(request_id))


def schedule_union_ack_expiry(
    context: ContextTypes.DEFAULT_TYPE | None,
    request_id: int,
    *,
    expires_at: datetime,
    when: float | None = None,
) -> None:
    jq = None
    if context is not None:
        jq = getattr(context, "job_queue", None)
    jq = jq or _resolve_job_queue()

    exp = _as_utc(expires_at)
    if exp is None:
        return

    name = _ack_expiry_job_name(int(request_id))
    if jq is not None:
        _cancel_jobs_by_name(jq, name)
        delay = float(when) if when is not None else None
        if delay is None:
            delay = max(0.0, (exp - datetime.now(timezone.utc)).total_seconds())
        jq.run_once(
            _union_ack_expiry_callback,
            when=delay,
            data={"request_id": int(request_id)},
            name=name,
            job_kwargs={"misfire_grace_time": 60},
        )
        logger.info(
            "union ack expiry: scheduled request_id=%s wait_s=%.1f",
            request_id,
            delay,
        )
        return

    logger.warning(
        "union ack expiry: no job_queue; DB row request_id=%s expires_at=%s",
        request_id,
        exp.isoformat(),
    )


def mark_union_instruction_expired(
    request_id: int,
) -> UnionInstructionExpiryTarget | None:
    """Persist expiry timestamp; return edit target if expiry should proceed."""
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = (
            session.query(ManualDepositRequest)
            .filter(ManualDepositRequest.id == int(request_id))
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            logger.info(
                "union instruction expiry: skip missing request_id=%s",
                request_id,
            )
            return None
        if row.instruction_expired_at is not None:
            return None
        if bool(row.trade_record_checked):
            row.instruction_expires_at = None
            session.flush()
            logger.info(
                "union instruction expiry: skip checked request_id=%s",
                request_id,
            )
            return None
        if row.instruction_expires_at is None:
            logger.warning(
                "union instruction expiry: skip no deadline request_id=%s chat_id=%s",
                request_id,
                row.telegram_chat_id,
            )
            return None
        row.instruction_expired_at = now
        row.instruction_expires_at = None
        session.flush()
        session.refresh(row)
        message_ids = [
            int(mid) for mid in (row.instruction_telegram_message_ids or []) if mid
        ]
        if not message_ids:
            logger.info(
                "union instruction expiry: no message ids request_id=%s chat_id=%s",
                request_id,
                row.telegram_chat_id,
            )
            return None
        return UnionInstructionExpiryTarget(
            telegram_chat_id=int(row.telegram_chat_id),
            instruction_telegram_message_ids=message_ids,
        )


async def expire_union_instruction_now(
    bot: Any,
    request_id: int,
) -> bool:
    """Apply expiry edit if still eligible. Returns True when messages were attempted."""
    target = mark_union_instruction_expired(int(request_id))
    if target is None:
        return False
    await _edit_instruction_messages(
        bot,
        chat_id=target.telegram_chat_id,
        message_ids=target.instruction_telegram_message_ids,
    )
    logger.info(
        "union instruction expiry: applied request_id=%s chat_id=%s messages=%s",
        request_id,
        target.telegram_chat_id,
        len(target.instruction_telegram_message_ids),
    )
    return True


async def edit_union_instruction_expired_messages(
    bot: Any,
    request_id: int,
) -> bool:
    """Edit instruction messages to expired copy (repair missed Telegram edits)."""
    with get_db() as session:
        row = session.get(ManualDepositRequest, int(request_id))
        if row is None:
            return False
        message_ids = [
            int(mid) for mid in (row.instruction_telegram_message_ids or []) if mid
        ]
        if not message_ids:
            return False
        chat_id = int(row.telegram_chat_id)
    await _edit_instruction_messages(
        bot,
        chat_id=chat_id,
        message_ids=message_ids,
    )
    logger.info(
        "union instruction expiry: re-applied edit request_id=%s chat_id=%s messages=%s",
        request_id,
        chat_id,
        len(message_ids),
    )
    return True


async def _union_instruction_expiry_callback(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    job = context.job
    if job is None or not job.data:
        return
    request_id = job.data.get("request_id")
    if request_id is None:
        return
    await expire_union_instruction_now(context.bot, int(request_id))


def schedule_union_instruction_expiry(
    context: ContextTypes.DEFAULT_TYPE | None,
    request_id: int,
    *,
    expires_at: datetime,
    when: float | None = None,
) -> None:
    """Schedule (or reschedule) expiry for a manual deposit instruction."""
    jq = None
    if context is not None:
        jq = getattr(context, "job_queue", None)
    jq = jq or _resolve_job_queue()

    exp = _as_utc(expires_at)
    if exp is None:
        return

    name = _instruction_expiry_job_name(int(request_id))
    if jq is not None:
        _cancel_jobs_by_name(jq, name)
        delay = float(when) if when is not None else None
        if delay is None:
            delay = max(0.0, (exp - datetime.now(timezone.utc)).total_seconds())
        jq.run_once(
            _union_instruction_expiry_callback,
            when=delay,
            data={"request_id": int(request_id)},
            name=name,
            job_kwargs={"misfire_grace_time": 60},
        )
        logger.info(
            "union instruction expiry: scheduled request_id=%s wait_s=%.1f",
            request_id,
            delay,
        )
        return

    logger.warning(
        "union instruction expiry: no job_queue; DB row request_id=%s expires_at=%s",
        request_id,
        exp.isoformat(),
    )


def restore_union_instruction_expiries(job_queue: Any | None = None) -> None:
    """Re-schedule pending instruction expiries after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    now = datetime.now(timezone.utc)
    for request_id, expires_at in list_pending_union_instruction_expiries():
        remaining = (expires_at - now).total_seconds()
        schedule_union_instruction_expiry(
            None,
            int(request_id),
            expires_at=expires_at,
            when=remaining,
        )


def restore_union_ack_expiries(job_queue: Any | None = None) -> None:
    """Re-schedule pending ack expiries after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    now = datetime.now(timezone.utc)
    for request_id, expires_at in list_pending_union_ack_expiries():
        remaining = (expires_at - now).total_seconds()
        schedule_union_ack_expiry(
            None,
            int(request_id),
            expires_at=expires_at,
            when=remaining,
        )


def restore_union_deposit_expiries(job_queue: Any | None = None) -> None:
    restore_union_ack_expiries(job_queue)
    restore_union_instruction_expiries(job_queue)


def _list_overdue_union_ack_request_ids(
    *,
    now: datetime | None = None,
) -> list[int]:
    cutoff = _as_utc(now) or datetime.now(timezone.utc)
    with get_db() as session:
        rows = (
            session.query(ManualDepositRequest.id)
            .filter(ManualDepositRequest.ack_expires_at.isnot(None))
            .filter(ManualDepositRequest.ack_expires_at <= cutoff)
            .filter(ManualDepositRequest.ack_expired_at.is_(None))
            .filter(ManualDepositRequest.acknowledged_at.is_(None))
            .filter(ManualDepositRequest.trade_record_checked.is_(False))
            .all()
        )
    return [int(request_id) for (request_id,) in rows]


def _list_overdue_union_instruction_request_ids(
    *,
    now: datetime | None = None,
) -> list[int]:
    cutoff = _as_utc(now) or datetime.now(timezone.utc)
    with get_db() as session:
        rows = (
            session.query(ManualDepositRequest.id)
            .filter(ManualDepositRequest.instruction_expires_at.isnot(None))
            .filter(ManualDepositRequest.instruction_expires_at <= cutoff)
            .filter(ManualDepositRequest.instruction_expired_at.is_(None))
            .filter(ManualDepositRequest.trade_record_checked.is_(False))
            .all()
        )
    return [int(request_id) for (request_id,) in rows]


async def sweep_overdue_union_deposit_expiries(bot: Any) -> None:
    """DB-driven fallback when one-shot jobs were missed."""
    for request_id in _list_overdue_union_ack_request_ids():
        await expire_union_ack_now(bot, int(request_id))
    for request_id in _list_overdue_union_instruction_request_ids():
        await expire_union_instruction_now(bot, int(request_id))


async def _union_deposit_expiry_sweep_callback(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await sweep_overdue_union_deposit_expiries(context.bot)
