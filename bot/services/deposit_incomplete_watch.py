"""DB-durable 10m incomplete-deposit watch (Slack escalation at reminder fire)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from db.connection import get_db
from db.models import DepositIncompleteWatch

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _deposit_reminder_seconds() -> int:
    from bot.handlers.deposit import DEPOSIT_REMINDER_SECONDS

    return int(DEPOSIT_REMINDER_SECONDS)


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    from bot.services.escalation_notification import _resolve_job_queue as esc_resolve

    return esc_resolve(None)


def list_armed_deposit_incomplete_watches() -> list[DepositIncompleteWatch]:
    with get_db() as session:
        return session.query(DepositIncompleteWatch).all()


def arm_deposit_incomplete_watch(
    *,
    telegram_chat_id: int,
    club_id: int | None,
    customer_telegram_user_id: int | None = None,
    group_title: str | None = None,
    armed_at: datetime | None = None,
) -> None:
    """Upsert one active incomplete-deposit watch row for a chat."""
    ts = _as_utc(armed_at) or datetime.now(timezone.utc)
    title = (group_title or "").strip() or None
    try:
        with get_db() as session:
            row = (
                session.query(DepositIncompleteWatch)
                .filter_by(telegram_chat_id=int(telegram_chat_id))
                .one_or_none()
            )
            if row is None:
                row = DepositIncompleteWatch(telegram_chat_id=int(telegram_chat_id))
                session.add(row)
            row.club_id = int(club_id) if club_id is not None else None
            row.customer_telegram_user_id = (
                int(customer_telegram_user_id)
                if customer_telegram_user_id is not None
                else None
            )
            row.group_title = title
            row.armed_at = ts
    except Exception:
        logger.exception(
            "deposit_incomplete_watch: arm failed chat_id=%s",
            telegram_chat_id,
        )


def delete_deposit_incomplete_watch(chat_id: int | str) -> None:
    try:
        with get_db() as session:
            session.query(DepositIncompleteWatch).filter_by(
                telegram_chat_id=int(chat_id)
            ).delete()
    except Exception:
        logger.exception(
            "deposit_incomplete_watch: delete failed chat_id=%s",
            chat_id,
        )


def cancel_deposit_incomplete_watch(
    chat_id: int | str,
    *,
    job_queue: Any | None = None,
) -> None:
    """Clear durable watch state (job cancel handled by deposit reminder cancel)."""
    delete_deposit_incomplete_watch(chat_id)


def payment_or_chips_seen_since_arm(chat_id: int, armed_at: datetime) -> bool:
    from bot.services.escalation_notification import _payment_seen_since_arm

    return bool(_payment_seen_since_arm(int(chat_id), armed_at))


async def notify_deposit_incomplete_escalation(
    *,
    chat_id: int,
    club_id: int | None,
    title: str | None = None,
) -> None:
    """Fire Slack incomplete-deposit escalation and delete the watch row."""
    from bot.services.escalation_notification import (
        REASON_DEPOSIT_INCOMPLETE,
        notify_escalation_slack,
    )

    try:
        await notify_escalation_slack(
            REASON_DEPOSIT_INCOMPLETE,
            club_id=int(club_id) if club_id is not None else None,
            chat_id=int(chat_id),
            title=title,
        )
    finally:
        delete_deposit_incomplete_watch(chat_id)


def _schedule_deposit_reminder_job(
    job_queue: Any,
    *,
    chat_id: int,
    club_id: int | None,
    armed_at: datetime,
    when: float,
    group_title: str | None = None,
) -> None:
    from bot.handlers.deposit import _deposit_reminder_callback, _reminder_job_name

    name = _reminder_job_name(chat_id)
    try:
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass

    delay = max(0.0, float(when))
    job_queue.run_once(
        _deposit_reminder_callback,
        when=delay,
        chat_id=int(chat_id),
        data={
            "club_id": club_id,
            "scheduled_at": armed_at.isoformat(),
            "group_title": (group_title or "").strip() or None,
        },
        name=name,
        job_kwargs={"misfire_grace_time": 60},
    )
    logger.info(
        "deposit_incomplete_watch: reminder restored chat_id=%s in %ss",
        chat_id,
        delay,
    )


def restore_deposit_incomplete_watches(job_queue: Any | None = None) -> None:
    """Re-schedule deposit reminder jobs after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        logger.warning("deposit_incomplete_watch: no job_queue for restore")
        return

    now = datetime.now(timezone.utc)
    wait = float(_deposit_reminder_seconds())

    for row in list_armed_deposit_incomplete_watches():
        chat_id = int(row.telegram_chat_id)
        armed_at = _as_utc(row.armed_at)
        if armed_at is None:
            delete_deposit_incomplete_watch(chat_id)
            continue

        if payment_or_chips_seen_since_arm(chat_id, armed_at):
            logger.info(
                "deposit_incomplete_watch: restore skip (payment/chips) chat_id=%s",
                chat_id,
            )
            delete_deposit_incomplete_watch(chat_id)
            continue

        elapsed = (now - armed_at).total_seconds()
        remaining = wait - elapsed
        _schedule_deposit_reminder_job(
            jq,
            chat_id=chat_id,
            club_id=int(row.club_id) if row.club_id is not None else None,
            armed_at=armed_at,
            when=remaining,
            group_title=row.group_title,
        )
