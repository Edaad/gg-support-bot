"""After a cashout is recorded, flag all-time $0 rake with a win.

The ClubGG rake read runs in the background. It does not delay the cashout
record, the owed pin, or the cooldown. All-time figures are ``rake.overall``
and ``pnl.overall`` from the same ``POST /rake`` call early feeback uses.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from bot.services.staff_cashout_records import (
    _HOLD_NOTE_HEADER,
    compute_ledger,
)
from db.connection import get_db
from db.models import StaffCashoutRecord

logger = logging.getLogger(__name__)

RAKE_HOLD_REASON = "No rake, won all time."
FLAG_TITLE = "No rake, won all time"
_UNVERIFIED_DM = "Rake couldn't be checked."


def classify_all_time_rake(
    rake_overall: Optional[Decimal], pnl_overall: Optional[Decimal]
) -> str:
    """``flag``, ``clear``, or ``unverified``.

    Flag only when all-time rake is exactly $0 and all-time PnL is a win.
    Missing numbers are unverified, not a clear.
    """
    if rake_overall is None or pnl_overall is None:
        return "unverified"
    if rake_overall == 0 and pnl_overall > 0:
        return "flag"
    return "clear"


def format_unverified_dm(group_title: str) -> str:
    title = (group_title or "").strip() or "Unknown group"
    return f"{_UNVERIFIED_DM}\nGroup: {title}"


def format_dollars(amount: Decimal, *, signed: bool = False) -> str:
    quantized = Decimal(amount).quantize(Decimal("0.01"))
    body = f"${abs(quantized):,.2f}"
    if quantized < 0:
        return f"-{body}"
    if signed and quantized > 0:
        return f"+{body}"
    return body


def format_flag_details(
    *,
    rake: Decimal,
    pnl: Decimal,
    cashout: Decimal,
    parked: bool,
) -> str:
    if parked:
        tail = f"Cashout {format_dollars(cashout)} set to do not send."
    else:
        tail = f"Cashout {format_dollars(cashout)} was already sent."
    return (
        f"All-time rake {format_dollars(rake)}. "
        f"All-time PnL {format_dollars(pnl, signed=True)}. "
        f"{tail}"
    )


def note_with_rake_reason(existing: str | None) -> str:
    text = (existing or "").strip()
    if RAKE_HOLD_REASON in text:
        return text
    if not text:
        return f"{_HOLD_NOTE_HEADER}\n{RAKE_HOLD_REASON}"
    if text.startswith(_HOLD_NOTE_HEADER):
        return f"{text}\n{RAKE_HOLD_REASON}"
    return f"{_HOLD_NOTE_HEADER}\n{RAKE_HOLD_REASON}\n{text}"


def load_cashout_rake_context(record_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if record is None:
            return None
        return {
            "record_id": int(record.id),
            "club_id": int(record.club_id),
            "chat_id": int(record.chat_id) if record.chat_id is not None else None,
            "group_title": record.group_title or "",
            "amount": Decimal(record.amount),
        }


def park_rake_hold(record_id: int) -> str:
    """``parked``, ``already``, ``sent``, or ``missing``.

    ``sent`` means the payout is no longer active, so do-not-send is left alone.
    """
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if record is None:
            return "missing"
        if RAKE_HOLD_REASON in (record.note or ""):
            return "already"
        sends = [{"amount": row.amount} for row in record.money_sends]
        ledger = compute_ledger(bool(record.tracks_money_sent), record.amount, sends)
        if ledger["status"] != "active":
            return "sent"
        record.do_not_send = True
        record.note = note_with_rake_reason(record.note)
        record.updated_at = datetime.utcnow()
        return "parked"


def schedule_cashout_rake_check(record_id: int) -> None:
    """Enqueue the rake read. Returns before the read starts waiting on ClubGG."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("cashout_rake_hold: no running loop record_id=%s", record_id)
        return
    task = loop.create_task(
        run_cashout_rake_check(int(record_id)),
        name=f"cashout-rake-{int(record_id)}",
    )
    task.add_done_callback(_log_task)


def _log_task(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("cashout_rake_hold: check failed")


async def run_cashout_rake_check(record_id: int) -> None:
    ctx = load_cashout_rake_context(int(record_id))
    if ctx is None:
        logger.warning("cashout_rake_hold: record missing id=%s", record_id)
        return

    outcome = await _read_rake(ctx)
    if outcome is None or not outcome.ok:
        await _dm_unverified(ctx)
        return

    kind = classify_all_time_rake(outcome.rake_overall, outcome.pnl_overall)
    if kind == "unverified":
        await _dm_unverified(ctx)
        return
    if kind != "flag":
        logger.info(
            "cashout_rake_hold: clear record_id=%s rake=%s pnl=%s",
            record_id,
            outcome.rake_overall,
            outcome.pnl_overall,
        )
        return

    result = park_rake_hold(int(record_id))
    if result in ("already", "missing"):
        return
    await _file_report(
        ctx,
        rake=outcome.rake_overall,
        pnl=outcome.pnl_overall,
        parked=result == "parked",
    )


async def _read_rake(ctx: dict[str, Any]):
    if ctx["chat_id"] is None:
        return None
    from bot.services.clubgg_deposit_api import run_rake_check

    try:
        return await run_rake_check(
            club_id=int(ctx["club_id"]),
            chat_id=int(ctx["chat_id"]),
            request_id=f"cashout-rake-{int(ctx['record_id'])}",
            group_title=ctx["group_title"] or None,
        )
    except Exception:
        logger.exception(
            "cashout_rake_hold: rake read failed record_id=%s", ctx["record_id"]
        )
        return None


async def _dm_unverified(ctx: dict[str, Any]) -> None:
    from club_gc_settings import get_club_gc_config_by_link_club_id

    cfg = get_club_gc_config_by_link_club_id(int(ctx["club_id"]))
    if cfg is None or not cfg.command_admin_user_id:
        logger.warning(
            "cashout_rake_hold: no support account record_id=%s club_id=%s",
            ctx["record_id"],
            ctx["club_id"],
        )
        return
    text = format_unverified_dm(ctx["group_title"])
    from bot.services import mtproto_track_contact as contact

    if contact._notify_bot is not None:
        await contact.notify_club_gc_admin_dm(cfg, text)
        return

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        logger.warning(
            "cashout_rake_hold: DM skipped, bot unavailable record_id=%s",
            ctx["record_id"],
        )
        return
    from telegram import Bot

    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=int(cfg.command_admin_user_id), text=text[:4096])
    except Exception:
        logger.exception(
            "cashout_rake_hold: DM failed record_id=%s club=%s",
            ctx["record_id"],
            cfg.club_key,
        )


async def _file_report(
    ctx: dict[str, Any],
    *,
    rake: Decimal,
    pnl: Decimal,
    parked: bool,
) -> None:
    from bot.services.issue_reports import create_issue_report

    description = format_flag_details(
        rake=rake, pnl=pnl, cashout=ctx["amount"], parked=parked
    )
    try:
        with get_db() as session:
            await create_issue_report(
                session,
                title=FLAG_TITLE,
                description=description,
                category="cashout",
                notify_tags=["head_admin"],
                reporter_name="GG Support Bot",
                reporter_source="cashout_rake_hold",
                club_id=int(ctx["club_id"]),
                group_title=ctx["group_title"] or None,
                telegram_chat_id=ctx["chat_id"],
            )
    except Exception:
        logger.exception(
            "cashout_rake_hold: issue report failed record_id=%s",
            ctx["record_id"],
        )
    else:
        logger.info(
            "cashout_rake_hold: flagged record_id=%s parked=%s",
            ctx["record_id"],
            parked,
        )
