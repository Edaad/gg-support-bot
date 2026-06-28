"""Worker batch: send outreach DMs to staged inactive players (armed via /sendinactive)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from club_gc_settings import (
    CLUB_GC_CONFIG,
    INACTIVE_OUTREACH_CLUB_KEYS,
    get_inactive_outreach_dm_batch_size,
    get_inactive_outreach_dm_delay_sec,
    get_inactive_outreach_dm_first_delay_sec,
    get_inactive_outreach_dm_interval_sec,
    is_inactive_outreach_dm_enabled,
)
from bot.services.inactive_group_outreach_staging import STAGE_STATUS_STAGED

logger = logging.getLogger(__name__)

_DM_BATCH_RUNNING = "running"
_DM_BATCH_COMPLETE = "complete"
_DM_BATCH_IDLE = "idle"

_OUTREACH_DM_APP: Any | None = None


@dataclass(frozen=True)
class DmOutreachRow:
    id: int
    club_key: str
    telegram_chat_id: int
    group_title: str
    player_telegram_user_id: int
    player_username: str | None
    player_display_name: str | None


def _ensure_control_row(session) -> Any:
    from db.models import InactiveGroupOutreachControl

    ctrl = session.get(InactiveGroupOutreachControl, 1)
    if ctrl is None:
        ctrl = InactiveGroupOutreachControl(id=1)
        session.add(ctrl)
    return ctrl


def is_dm_batch_running() -> bool:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return False
        return str(ctrl.dm_batch_status or "") == _DM_BATCH_RUNNING


def count_dm_eligible_recipients(
    *,
    club_key: str,
    row_id: int | None = None,
    limit: int | None = None,
) -> int:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        query = _eligible_query(session, club_key, row_id=row_id)
        if limit is not None:
            return min(int(limit), query.count())
        return query.count()


def _eligible_query(session, club_key: str, *, row_id: int | None = None):
    from db.models import InactiveGroupOutreachRow
    from sqlalchemy import or_

    query = session.query(InactiveGroupOutreachRow).filter(
        InactiveGroupOutreachRow.club_key == club_key,
        InactiveGroupOutreachRow.stage_status == STAGE_STATUS_STAGED,
        InactiveGroupOutreachRow.entity_resolvable.is_(True),
        InactiveGroupOutreachRow.player_telegram_user_id.isnot(None),
        or_(
            InactiveGroupOutreachRow.dm_status.is_(None),
            InactiveGroupOutreachRow.dm_status == "",
            InactiveGroupOutreachRow.dm_status == "pending",
            InactiveGroupOutreachRow.dm_status == "failed",
        ),
    )
    if row_id is not None:
        query = query.filter(InactiveGroupOutreachRow.id == int(row_id))
    return query


def arm_dm_campaign(
    *,
    club_key: str,
    message: str,
    started_by_user_id: int,
    row_id: int | None = None,
    limit: int | None = None,
) -> tuple[bool, str, int]:
    """Persist campaign message and mark eligible rows pending. Returns (ok, error, count)."""

    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl, InactiveGroupOutreachRow

    body = (message or "").strip()
    if not body:
        return False, "Message is empty.", 0
    if club_key not in INACTIVE_OUTREACH_CLUB_KEYS:
        return False, f"Unknown club_key: {club_key!r}.", 0

    now = datetime.now(timezone.utc)
    with get_db() as session:
        ctrl = _ensure_control_row(session)
        if str(ctrl.dm_batch_status or "") == _DM_BATCH_RUNNING:
            return False, "A DM batch is already running. Wait for it to finish.", 0

        eligible = _eligible_query(session, club_key, row_id=row_id)
        rows = eligible.order_by(InactiveGroupOutreachRow.id.asc()).all()
        if limit is not None:
            rows = rows[: max(1, int(limit))]

        if not rows:
            return False, "No eligible staged recipients found.", 0

        for row in rows:
            row.dm_status = "pending"
            row.dm_error = None
            row.updated_at = now

        ctrl.dm_campaign_message = body
        ctrl.dm_batch_status = _DM_BATCH_RUNNING
        ctrl.dm_campaign_started_at = now
        ctrl.dm_campaign_started_by_telegram_user_id = int(started_by_user_id)
        ctrl.dm_sent_count = 0
        ctrl.dm_failed_count = 0
        session.commit()
        return True, "", len(rows)


def claim_dm_batch(club_key: str, limit: int) -> list[DmOutreachRow]:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        rows = (
            session.query(InactiveGroupOutreachRow)
            .filter(
                InactiveGroupOutreachRow.club_key == club_key,
                InactiveGroupOutreachRow.stage_status == STAGE_STATUS_STAGED,
                InactiveGroupOutreachRow.entity_resolvable.is_(True),
                InactiveGroupOutreachRow.player_telegram_user_id.isnot(None),
                InactiveGroupOutreachRow.dm_status == "pending",
            )
            .order_by(InactiveGroupOutreachRow.id.asc())
            .limit(limit)
            .all()
        )
        return [
            DmOutreachRow(
                id=int(r.id),
                club_key=str(r.club_key),
                telegram_chat_id=int(r.telegram_chat_id),
                group_title=str(r.group_title),
                player_telegram_user_id=int(r.player_telegram_user_id),
                player_username=str(r.player_username) if r.player_username else None,
                player_display_name=str(r.player_display_name) if r.player_display_name else None,
            )
            for r in rows
        ]


def _count_dm_pending() -> int:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        return (
            session.query(InactiveGroupOutreachRow)
            .filter(InactiveGroupOutreachRow.dm_status == "pending")
            .count()
        )


def _get_campaign_message() -> str | None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return None
        msg = (ctrl.dm_campaign_message or "").strip()
        return msg or None


def _persist_dm_result(row_id: int, *, sent: bool, error: str | None = None) -> None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl, InactiveGroupOutreachRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(InactiveGroupOutreachRow, int(row_id))
        if row is not None:
            if sent:
                row.dm_status = "sent"
                row.dm_error = None
                row.dm_sent_at = now
            else:
                row.dm_status = "failed"
                row.dm_error = (error or "unknown")[:2000]
            row.updated_at = now

        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is not None:
            if sent:
                ctrl.dm_sent_count = int(ctrl.dm_sent_count or 0) + 1
            else:
                ctrl.dm_failed_count = int(ctrl.dm_failed_count or 0) + 1
            ctrl.last_tick_at = now
        session.commit()


def _finish_dm_batch() -> tuple[int, int, int | None]:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    now = datetime.now(timezone.utc)
    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return 0, 0, None
        sent = int(ctrl.dm_sent_count or 0)
        failed = int(ctrl.dm_failed_count or 0)
        staff_id = (
            int(ctrl.dm_campaign_started_by_telegram_user_id)
            if ctrl.dm_campaign_started_by_telegram_user_id is not None
            else None
        )
        ctrl.dm_batch_status = _DM_BATCH_COMPLETE
        ctrl.last_tick_at = now
        session.commit()
        return sent, failed, staff_id


async def _notify_staff_dm_complete(
    ptb_bot,
    staff_user_id: int | None,
    *,
    sent: int,
    failed: int,
) -> None:
    if ptb_bot is None or staff_user_id is None:
        return
    text = (
        f"Inactive outreach DM batch finished.\n"
        f"sent={sent} failed={failed}"
    )
    try:
        await ptb_bot.send_message(int(staff_user_id), text)
    except Exception:
        logger.warning(
            "inactive_outreach_dm: staff completion DM failed user_id=%s",
            staff_user_id,
            exc_info=True,
        )


async def _notify_completion_slack(*, sent: int, failed: int) -> None:
    from bot.services.slack_ops_notify import notify_slack_ops

    detail = (
        f"<b>Inactive outreach DM batch complete</b>\n"
        f"sent={sent} failed={failed}"
    )
    await notify_slack_ops(detail, source="inactive_outreach_dm")


async def _send_one_dm(
    client,
    cfg,
    row: DmOutreachRow,
    message: str,
) -> tuple[bool, str | None]:
    from bot.services.migration_group_readd import call_with_flood_retry, is_entity_resolution_error

    player_id = int(row.player_telegram_user_id)

    async def _resolve():
        return await client.get_entity(player_id)

    try:
        player_ent = await call_with_flood_retry(
            _resolve,
            label=f"inactive_outreach_dm:get_player:{player_id}",
        )
    except Exception as exc:
        if is_entity_resolution_error(exc):
            return False, type(exc).__name__
        raise

    async def _send():
        await client.send_message(player_ent, message)

    try:
        await call_with_flood_retry(
            _send,
            label=f"inactive_outreach_dm:send:{player_id}",
        )
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


async def tick_async(ptb_bot=None) -> dict[str, int]:
    if not is_inactive_outreach_dm_enabled():
        return {"processed": 0}

    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None or str(ctrl.dm_batch_status or "") != _DM_BATCH_RUNNING:
            return {"processed": 0}

    message = _get_campaign_message()
    if not message:
        logger.warning("inactive_outreach_dm: running batch but no campaign message")
        return {"processed": 0}

    summary = {"processed": 0, "failed": 0}
    batch_size = get_inactive_outreach_dm_batch_size()
    delay_sec = get_inactive_outreach_dm_delay_sec()

    from bot.services.mtproto_dm_gc_listener import get_listener_client

    for club_key in INACTIVE_OUTREACH_CLUB_KEYS:
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            continue
        client = get_listener_client(club_key)
        if client is None or not client.is_connected():
            logger.info("inactive_outreach_dm: skip club=%s listener down", club_key)
            continue

        rows = claim_dm_batch(club_key, batch_size)
        if not rows:
            continue

        for row in rows:
            ok, err = await _send_one_dm(client, cfg, row, message)
            _persist_dm_result(row.id, sent=ok, error=err)
            if ok:
                summary["processed"] += 1
            else:
                summary["failed"] += 1
                logger.warning(
                    "inactive_outreach_dm: send failed row_id=%s player=%s err=%s",
                    row.id,
                    row.player_telegram_user_id,
                    err,
                )
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)

    if _count_dm_pending() == 0:
        sent, failed, staff_id = _finish_dm_batch()
        remove_inactive_outreach_dm_job()
        await _notify_completion_slack(sent=sent, failed=failed)
        await _notify_staff_dm_complete(ptb_bot, staff_id, sent=sent, failed=failed)
        logger.info("inactive_outreach_dm: batch complete sent=%s failed=%s", sent, failed)

    return summary


def remove_inactive_outreach_dm_job() -> None:
    if _OUTREACH_DM_APP is None:
        return
    jobs = _OUTREACH_DM_APP.job_queue.get_jobs_by_name("inactive_group_outreach_dm")
    for job in jobs:
        job.schedule_removal()


def schedule_inactive_outreach_dm_tick(ptb_bot=None) -> None:
    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        logger.warning("inactive_outreach_dm: listener loop not running; skipping tick")
        return
    asyncio.run_coroutine_threadsafe(tick_async(ptb_bot), loop)


def inactive_outreach_dm_job_callback(context) -> None:
    ptb_bot = getattr(context.application, "bot", None) if context.application else None
    schedule_inactive_outreach_dm_tick(ptb_bot)


def setup_inactive_group_outreach_dm_job(app) -> None:
    """Schedule DM batch ticks when a campaign is armed and env switch is on."""

    global _OUTREACH_DM_APP

    from club_gc_settings import is_dm_gc_listener_enabled

    if not is_dm_gc_listener_enabled():
        return
    if not is_inactive_outreach_dm_enabled():
        return
    if not is_dm_batch_running():
        return

    _OUTREACH_DM_APP = app
    remove_inactive_outreach_dm_job()
    interval_sec = get_inactive_outreach_dm_interval_sec()
    first_delay_sec = get_inactive_outreach_dm_first_delay_sec()
    app.job_queue.run_repeating(
        inactive_outreach_dm_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name="inactive_group_outreach_dm",
    )
    logger.info(
        "inactive_outreach_dm job scheduled first_delay_sec=%s interval_sec=%s batch_size=%s",
        first_delay_sec,
        interval_sec,
        get_inactive_outreach_dm_batch_size(),
    )


def start_dm_batch_job_if_armed(app) -> None:
    """Called after /sendinactive confirm to begin worker ticks."""

    global _OUTREACH_DM_APP
    _OUTREACH_DM_APP = app
    if not is_inactive_outreach_dm_enabled():
        logger.warning(
            "inactive_outreach_dm: campaign armed but GC_INACTIVE_OUTREACH_DM_ENABLED is off"
        )
        return
    setup_inactive_group_outreach_dm_job(app)
