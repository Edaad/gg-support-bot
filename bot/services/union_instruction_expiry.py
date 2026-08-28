"""Durable 10-minute expiry for union manual-deposit instruction messages."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker
from db.connection import get_db
from db.models import ManualDepositRequest

logger = logging.getLogger(__name__)

UNION_INSTRUCTION_EXPIRY_SECONDS = 600  # 10 minutes
UNION_INSTRUCTION_EXPIRY_SECONDS_TEST = 30

UNION_INSTRUCTION_EXPIRED_TEXT = (
    "This payment tag has expired.\n\n"
    "If you've already sent the payment, please let us know here and we'll take a look.\n\n"
    "This was a one-time address, so please DO NOT send money there again.\n\n"
    "Whenever you're ready, use /deposit to get new instructions."
)

_union_expiry_app: Any | None = None


def union_instruction_expiry_seconds() -> int:
    if is_test_bot_worker():
        return UNION_INSTRUCTION_EXPIRY_SECONDS_TEST
    return UNION_INSTRUCTION_EXPIRY_SECONDS


def register_union_instruction_expiry_runtime(app: Any) -> None:
    global _union_expiry_app
    _union_expiry_app = app
    try:
        restore_union_instruction_expiries(getattr(app, "job_queue", None))
    except Exception:
        logger.warning(
            "union instruction expiry: restore pending jobs failed", exc_info=True
        )


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    if _union_expiry_app is not None:
        return getattr(_union_expiry_app, "job_queue", None)
    return None


def _expiry_job_name(request_id: int) -> str:
    return f"union_instruction_expire_{int(request_id)}"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def cancel_union_instruction_expiry(
    request_id: int,
    *,
    job_queue: Any | None = None,
) -> None:
    """Stop a pending expiry (e.g. ops marked trade record checked)."""
    jq = _resolve_job_queue(job_queue)
    name = _expiry_job_name(int(request_id))
    if jq is not None:
        try:
            for job in jq.get_jobs_by_name(name):
                job.schedule_removal()
        except Exception:
            logger.debug(
                "union instruction expiry: cancel job failed request_id=%s",
                request_id,
                exc_info=True,
            )

    with get_db() as session:
        row = session.get(ManualDepositRequest, int(request_id))
        if row is None:
            return
        row.instruction_expires_at = None
        session.flush()


async def _edit_instruction_messages(
    bot: Any,
    *,
    chat_id: int,
    message_ids: list[int],
) -> None:
    for message_id in message_ids:
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=UNION_INSTRUCTION_EXPIRED_TEXT,
            )
        except Exception:
            logger.debug(
                "union instruction expiry: edit failed chat_id=%s message_id=%s",
                chat_id,
                message_id,
                exc_info=True,
            )


def mark_union_instruction_expired(request_id: int) -> ManualDepositRequest | None:
    """Persist expiry timestamp; return row if expiry should proceed."""
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
        if row.instruction_expired_at is not None:
            return None
        if bool(row.trade_record_checked):
            row.instruction_expires_at = None
            session.flush()
            return None
        if row.instruction_expires_at is None:
            return None
        row.instruction_expired_at = now
        row.instruction_expires_at = None
        session.flush()
        session.refresh(row)
        return row


async def expire_union_instruction_now(
    bot: Any,
    request_id: int,
) -> bool:
    """Apply expiry edit if still eligible. Returns True when messages were attempted."""
    row = mark_union_instruction_expired(int(request_id))
    if row is None:
        return False
    message_ids = [
        int(mid) for mid in (row.instruction_telegram_message_ids or []) if mid
    ]
    if not message_ids:
        logger.info(
            "union instruction expiry: no message ids request_id=%s chat_id=%s",
            request_id,
            row.telegram_chat_id,
        )
        return False
    await _edit_instruction_messages(
        bot,
        chat_id=int(row.telegram_chat_id),
        message_ids=message_ids,
    )
    logger.info(
        "union instruction expiry: applied request_id=%s chat_id=%s messages=%s",
        request_id,
        row.telegram_chat_id,
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

    name = _expiry_job_name(int(request_id))
    if jq is not None:
        try:
            for job in jq.get_jobs_by_name(name):
                job.schedule_removal()
        except Exception:
            pass

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
    """Re-schedule pending expiries after worker restart."""
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


def instruction_expires_at_from_now(
    *,
    now: datetime | None = None,
) -> datetime:
    base = _as_utc(now) or datetime.now(timezone.utc)
    return base + timedelta(seconds=union_instruction_expiry_seconds())
