"""Initiate a group /cash cashout job (shared by support bot and MTProto)."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from cashier.services.jobs import create_job, get_job
from cashier.services.notify import (
    notify_staff_cashout_job,
    notify_staff_claim_waiting,
)

logger = logging.getLogger(__name__)

WORKING_ON_CASHOUT_MESSAGE = "Working on your cashout"


def _schedule_coro(coro, *, name: str) -> None:
    """Run a coroutine whether or not the caller has a running event loop.

    - Support-bot path: called inside a PTB handler (running loop) -> create_task.
    - MTProto path: called via ``asyncio.to_thread`` (no running loop) -> hand off to
      the MTProto listener loop when available, else run it to completion here.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro, name=name)
        return
    except RuntimeError:
        pass

    try:
        from bot.services.mtproto_dm_gc_listener import _loop_holder

        loop = _loop_holder.get("loop")
    except Exception:
        loop = None

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        asyncio.run(coro)


def _claim_failure_note(outcome) -> str:
    """Human-readable warning line for a failed/uncertain auto claim."""
    amount = outcome.amount_str or "?"
    if outcome.status == "uncertain":
        return (
            f"\u26a0\ufe0f Auto-claim UNCERTAIN ({amount} chips): {outcome.reason}. "
            f"A claim may have gone through \u2014 DO NOT re-claim; verify on ClubGG "
            f"before recording."
        )
    if outcome.status == "no_machine":
        return (
            f"\u26a0\ufe0f Auto-claim skipped: no deposit machine online ({outcome.reason}). "
            f"Claim the chips manually on ClubGG, then continue."
        )
    if outcome.status == "unmapped":
        return (
            f"\u26a0\ufe0f Auto-claim skipped: {outcome.reason}. "
            f"Claim the chips manually on ClubGG, then continue."
        )
    return (
        f"\u26a0\ufe0f Auto-claim {outcome.status.upper()} ({amount} chips): "
        f"{outcome.reason}. Claim the chips manually on ClubGG, then continue."
    )


async def _claim_then_notify(
    *,
    chat_id: int,
    club_id: int,
    group_title: str,
    amount: Decimal,
    initiated_by: int,
    job_id: int,
) -> None:
    """Optionally auto-claim chips back, then DM staff the Continue button.

    Fail-safe: any problem degrades to the normal notify so a cashout is never blocked.
    """
    do_claim = False
    player_id = None
    try:
        from bot.services.clubgg_deposit_api import deposit_api_configured
        from bot.services.club import get_auto_claim_enabled
        from bot.services.player_details import gg_player_id_from_title

        if deposit_api_configured():
            enabled = await asyncio.to_thread(get_auto_claim_enabled, int(club_id))
            if enabled:
                player_id = gg_player_id_from_title(group_title)
                do_claim = bool(player_id)
                if not player_id:
                    logger.warning(
                        "group_cash: auto-claim enabled but no player id in title "
                        "job_id=%s title=%r",
                        job_id,
                        group_title,
                    )
    except Exception:
        logger.exception("group_cash: auto-claim precheck failed job_id=%s", job_id)
        do_claim = False

    async def _plain_notify(note: str | None = None) -> None:
        ok = await notify_staff_cashout_job(
            staff_user_id=initiated_by,
            job_id=job_id,
            group_title=group_title,
            amount=amount,
            note=note,
        )
        if not ok:
            logger.warning(
                "group_cash notify failed job_id=%s staff=%s — staff may not see "
                "Continue button",
                job_id,
                initiated_by,
            )

    if not do_claim:
        await _plain_notify()
        return

    waiting_id = await notify_staff_claim_waiting(
        staff_user_id=initiated_by,
        job_id=job_id,
        group_title=group_title,
        amount=amount,
        player_id=player_id,
    )

    from bot.services.clubgg_deposit_api import run_auto_claim

    outcome = await run_auto_claim(
        club_id=int(club_id),
        chat_id=int(chat_id),
        job_id=int(job_id),
        amount=amount,
        group_title=group_title,
    )

    # Don't override if the job was cancelled/completed while the claim was running.
    try:
        current = await asyncio.to_thread(get_job, int(job_id))
    except Exception:
        current = None
    if current and current.get("status") in ("cancelled", "completed"):
        logger.info(
            "group_cash: job %s already %s after claim (status=%s) — skipping Continue",
            job_id,
            current.get("status"),
            outcome.status,
        )
        return

    if outcome.ok:
        note = (
            f"\u2705 Claimed {outcome.amount_str} back on "
            f"{outcome.clubgg_club} (player {outcome.player_id})."
        )
    else:
        note = _claim_failure_note(outcome)

    ok = await notify_staff_cashout_job(
        staff_user_id=initiated_by,
        job_id=job_id,
        group_title=group_title,
        amount=amount,
        note=note,
        edit_message_id=waiting_id,
    )
    if not ok:
        logger.warning(
            "group_cash notify (post-claim) failed job_id=%s staff=%s",
            job_id,
            initiated_by,
        )


def initiate_group_cash_job(
    *,
    chat_id: int,
    club_id: int,
    group_title: str,
    amount: Decimal,
    initiated_by: int,
) -> int:
    """Create job, optionally auto-claim chips back, and notify staff. Returns job id."""
    job = create_job(
        club_id=club_id,
        chat_id=chat_id,
        group_title=group_title or "Unknown group",
        amount=amount,
        initiated_by=initiated_by,
        trigger="group_cash",
    )
    job_id = int(job["id"])
    logger.info(
        "group_cash initiated job_id=%s chat_id=%s club_id=%s amount=%s staff=%s",
        job_id,
        chat_id,
        club_id,
        amount,
        initiated_by,
    )

    _schedule_coro(
        _claim_then_notify(
            chat_id=chat_id,
            club_id=club_id,
            group_title=group_title or "Unknown group",
            amount=amount,
            initiated_by=initiated_by,
            job_id=job_id,
        ),
        name=f"cashout-claim-notify-{job_id}",
    )

    logger.debug("group_cash claim/notify scheduled job_id=%s", job_id)
    return job_id
