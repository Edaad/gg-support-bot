"""Finish chip-adds that were saved and then lost, and drop the ones past their ttl."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.services.clubgg_rpa_events import (
    CHIP_ADD_SWEEP_GRACE,
    due_pending_chip_adds,
    expire_elapsed_chip_adds,
)

logger = logging.getLogger(__name__)

JOB_POLL_INTERVAL = timedelta(seconds=5)


def payment_from_request_id(request_id: str) -> tuple[str, int] | None:
    """payment-zelle-4723 -> ('zelle', 4723)."""
    if not (request_id or "").startswith("payment-"):
        return None
    slug, sep, raw_id = request_id[len("payment-") :].rpartition("-")
    if not sep or not slug or not raw_id.isdigit():
        return None
    return slug, int(raw_id)


def _cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


async def resume_due_chip_adds(now: datetime | None = None) -> int:
    """Expire lapsed chip-adds, then send pending ones the saver never claimed."""
    from bot.services.clubgg_deposit_api import run_auto_chip_add
    from bot.services.escalation_notification import notify_rpa_deposit_failed
    from bot.services.payment_auto_deposit import maybe_auto_deposit_from_payment

    current = now or datetime.now(timezone.utc)
    expired = await asyncio.to_thread(expire_elapsed_chip_adds, current)
    for row in expired:
        chat_id = row.get("telegram_chat_id")
        if chat_id is None:
            continue
        try:
            await notify_rpa_deposit_failed(
                club_id=row.get("club_id"),
                chat_id=int(chat_id),
                title=row.get("group_title"),
            )
        except Exception:
            logger.exception(
                "chip_add_recovery: expire alert failed request_id=%s",
                row.get("request_id"),
            )

    due = await asyncio.to_thread(due_pending_chip_adds, current)
    resumed = 0
    for row in due:
        if (
            row.get("club_id") is None
            or row.get("telegram_chat_id") is None
            or row.get("amount") is None
        ):
            continue
        request_id = row["request_id"]
        try:
            parsed = payment_from_request_id(request_id)
            if parsed is not None:
                slug, payment_id = parsed
                await maybe_auto_deposit_from_payment(
                    club_id=int(row["club_id"]),
                    telegram_chat_id=int(row["telegram_chat_id"]),
                    amount_cents=_cents(row["amount"]),
                    auto_bound=True,
                    payment_method_slug=slug,
                    payment_id=payment_id,
                    group_title=row.get("group_title"),
                    already_queued=True,
                )
            else:
                await run_auto_chip_add(
                    club_id=int(row["club_id"]),
                    chat_id=int(row["telegram_chat_id"]),
                    amount=row["amount"],
                    request_id=request_id,
                    group_title=row.get("group_title"),
                )
        except Exception:
            logger.exception(
                "chip_add_recovery: resume failed request_id=%s", request_id
            )
            continue
        resumed += 1
        logger.info("chip_add_recovery: resumed request_id=%s", request_id)
    return resumed


async def chip_add_recovery_job_callback(context) -> None:
    del context
    try:
        resumed = await resume_due_chip_adds()
        if resumed:
            logger.info("chip_add_recovery job resumed count=%s", resumed)
    except Exception:
        logger.exception("chip_add_recovery job failed")


def schedule_chip_add_recovery_job(app) -> None:
    if app.job_queue is None:
        logger.warning("chip_add_recovery: job_queue unavailable")
        return
    app.job_queue.run_repeating(
        chip_add_recovery_job_callback,
        interval=JOB_POLL_INTERVAL,
        first=JOB_POLL_INTERVAL,
        name="chip_add_recovery",
    )
    logger.info(
        "chip_add_recovery job scheduled interval_sec=%s grace_sec=%s",
        int(JOB_POLL_INTERVAL.total_seconds()),
        int(CHIP_ADD_SWEEP_GRACE.total_seconds()),
    )
