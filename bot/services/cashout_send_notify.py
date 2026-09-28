"""Bot-worker queue for cashout money-send player notifications and owed-pin clears.

The dashboard (web dyno) cannot talk as the club MTProto account: a second
connection on the same auth key trips AUTH_KEY_DUPLICATED and drops the dm/gc
listener. So the API only marks rows ``pending`` and this worker job does the
Telegram side on the live listener client:

- ``staff_cashout_money_sends.notify_status='pending'`` → post ``Sent $X!`` in the
  player's group (with the screenshot, or the crypto transaction link).
- ``staff_cashout_records.owed_clear_status='pending'`` → edit the original
  ``$X owed`` pin to ``$0 owed`` and unpin it.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from club_gc_settings import get_club_gc_config_by_link_club_id

logger = logging.getLogger(__name__)

JOB_NAME = "cashout_send_notify"
TICK_INTERVAL_SEC = 10
BATCH_SIZE = 10
PINNED_LOOKUP_LIMIT = 50

_tick_lock = asyncio.Lock()


@dataclass(frozen=True)
class PendingSend:
    id: int
    record_id: int
    club_id: int
    chat_id: Optional[int]
    amount: Decimal
    proof_link: Optional[str]
    has_proof: bool


@dataclass(frozen=True)
class PendingOwedClear:
    record_id: int
    club_id: int
    chat_id: Optional[int]
    amount: Decimal
    owed_message_id: Optional[int]


def _load_pending_sends(limit: int) -> list[PendingSend]:
    from db.connection import get_db
    from db.models import StaffCashoutMoneySend, StaffCashoutRecord

    with get_db() as session:
        rows = (
            session.query(StaffCashoutMoneySend, StaffCashoutRecord)
            .join(
                StaffCashoutRecord,
                StaffCashoutRecord.id == StaffCashoutMoneySend.cashout_record_id,
            )
            .filter(StaffCashoutMoneySend.notify_status == "pending")
            .order_by(StaffCashoutMoneySend.id.asc())
            .limit(limit)
            .all()
        )
        return [
            PendingSend(
                id=int(send.id),
                record_id=int(record.id),
                club_id=int(record.club_id),
                chat_id=int(record.chat_id) if record.chat_id is not None else None,
                amount=Decimal(str(send.amount)),
                proof_link=send.proof_link,
                has_proof=bool(send.has_proof),
            )
            for send, record in rows
        ]


def _load_proof(send_id: int) -> Optional[tuple[bytes, str, str]]:
    from db.connection import get_db
    from db.models import StaffCashoutSendProof

    with get_db() as session:
        proof = (
            session.query(StaffCashoutSendProof)
            .filter(StaffCashoutSendProof.money_send_id == int(send_id))
            .first()
        )
        if proof is None:
            return None
        return bytes(proof.content), proof.filename, proof.content_type


def _mark_send(send_id: int, *, ok: bool, error: Optional[str] = None) -> None:
    from db.connection import get_db
    from db.models import StaffCashoutMoneySend

    with get_db() as session:
        row = session.get(StaffCashoutMoneySend, int(send_id))
        if row is None:
            return
        row.notify_status = "sent" if ok else "failed"
        row.notify_error = None if ok else (error or "unknown error")[:500]
        if ok:
            row.notified_at = datetime.utcnow()


def _load_pending_owed_clears(limit: int) -> list[PendingOwedClear]:
    from db.connection import get_db
    from db.models import StaffCashoutRecord

    with get_db() as session:
        rows = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.owed_clear_status == "pending")
            .order_by(StaffCashoutRecord.id.asc())
            .limit(limit)
            .all()
        )
        return [
            PendingOwedClear(
                record_id=int(r.id),
                club_id=int(r.club_id),
                chat_id=int(r.chat_id) if r.chat_id is not None else None,
                amount=Decimal(str(r.amount)),
                owed_message_id=(
                    int(r.owed_message_id) if r.owed_message_id is not None else None
                ),
            )
            for r in rows
        ]


def _mark_owed_clear(
    record_id: int,
    *,
    ok: bool,
    error: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    from db.connection import get_db
    from db.models import StaffCashoutRecord

    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if record is None or record.owed_clear_status != "pending":
            return
        record.owed_clear_status = "done" if ok else "failed"
        record.owed_clear_error = None if ok else (error or "unknown error")[:500]
        if message_id is not None and record.owed_message_id is None:
            record.owed_message_id = int(message_id)
        if ok:
            record.owed_cleared_at = datetime.utcnow()


def _live_client(club_id: int):
    """``(cfg, client)`` for the club's connected listener; ``client`` None when down."""
    from bot.services.mtproto_dm_gc_listener import get_listener_client

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is None:
        return None, None
    client = get_listener_client(cfg.club_key)
    if client is None or not client.is_connected():
        return cfg, None
    return cfg, client


async def _post_send(client, send: PendingSend) -> None:
    from bot.services.mtproto_group_cash import format_sent_caption

    caption = format_sent_caption(send.amount)
    proof = _load_proof(send.id) if send.has_proof else None
    if proof is not None:
        content, filename, _content_type = proof
        buf = io.BytesIO(content)
        buf.name = filename or "screenshot.jpg"
        await client.send_file(send.chat_id, buf, caption=caption)
    elif send.proof_link:
        await client.send_message(
            send.chat_id, f"{caption}\n{send.proof_link}", link_preview=False
        )
    else:
        await client.send_message(send.chat_id, caption)


async def process_pending_sends() -> dict[str, int]:
    from bot.services.mtproto_group_create import get_mtproto_lock

    summary = {"sent": 0, "failed": 0, "waiting": 0}
    for send in _load_pending_sends(BATCH_SIZE):
        if send.chat_id is None:
            _mark_send(send.id, ok=False, error="No group chat connected")
            summary["failed"] += 1
            continue
        cfg, client = _live_client(send.club_id)
        if cfg is None:
            _mark_send(send.id, ok=False, error="Club Telegram account not configured")
            summary["failed"] += 1
            continue
        if client is None:
            summary["waiting"] += 1  # listener down; retry next tick
            continue
        try:
            async with get_mtproto_lock(cfg.club_key):
                await _post_send(client, send)
        except Exception as e:
            logger.warning(
                "cashout_send_notify: send failed send_id=%s chat_id=%s err=%s: %s",
                send.id,
                send.chat_id,
                type(e).__name__,
                e,
            )
            _mark_send(send.id, ok=False, error=f"{type(e).__name__}: {e}")
            summary["failed"] += 1
            continue
        _mark_send(send.id, ok=True)
        summary["sent"] += 1
    return summary


async def _find_owed_pin(client, chat_id: int, owed_text: str) -> Optional[int]:
    """Newest pinned message from this account reading exactly ``owed_text``."""
    from telethon.tl.types import InputMessagesFilterPinned

    async for msg in client.iter_messages(
        chat_id,
        limit=PINNED_LOOKUP_LIMIT,
        filter=InputMessagesFilterPinned,
        from_user="me",
    ):
        if (getattr(msg, "message", None) or "").strip() == owed_text:
            return int(msg.id)
    return None


async def process_pending_owed_clears() -> dict[str, int]:
    from bot.services.mtproto_group_cash import format_cash_owed
    from bot.services.mtproto_group_create import get_mtproto_lock

    summary = {"cleared": 0, "failed": 0, "waiting": 0}
    for item in _load_pending_owed_clears(BATCH_SIZE):
        if item.chat_id is None:
            _mark_owed_clear(item.record_id, ok=False, error="No group chat connected")
            summary["failed"] += 1
            continue
        cfg, client = _live_client(item.club_id)
        if cfg is None:
            _mark_owed_clear(
                item.record_id, ok=False, error="Club Telegram account not configured"
            )
            summary["failed"] += 1
            continue
        if client is None:
            summary["waiting"] += 1
            continue
        message_id = item.owed_message_id
        try:
            async with get_mtproto_lock(cfg.club_key):
                if message_id is None:
                    message_id = await _find_owed_pin(
                        client, item.chat_id, format_cash_owed(item.amount)
                    )
                if message_id is None:
                    _mark_owed_clear(
                        item.record_id, ok=False, error="Owed message not found"
                    )
                    summary["failed"] += 1
                    continue
                await client.edit_message(
                    item.chat_id, message_id, format_cash_owed(Decimal("0"))
                )
                await client.unpin_message(item.chat_id, message_id)
        except Exception as e:
            logger.warning(
                "cashout_send_notify: owed clear failed record_id=%s chat_id=%s "
                "err=%s: %s",
                item.record_id,
                item.chat_id,
                type(e).__name__,
                e,
            )
            _mark_owed_clear(
                item.record_id,
                ok=False,
                error=f"{type(e).__name__}: {e}",
                message_id=message_id,
            )
            summary["failed"] += 1
            continue
        _mark_owed_clear(item.record_id, ok=True, message_id=message_id)
        summary["cleared"] += 1
    return summary


async def tick_async() -> dict[str, Any]:
    """One pass: player notifications first, then owed-pin clears."""
    if _tick_lock.locked():
        return {"skipped": True}
    async with _tick_lock:
        out: dict[str, Any] = {}
        try:
            out["sends"] = await process_pending_sends()
        except Exception:
            logger.exception("cashout_send_notify: send pass crashed")
        try:
            out["owed"] = await process_pending_owed_clears()
        except Exception:
            logger.exception("cashout_send_notify: owed clear pass crashed")
        return out


def cashout_send_notify_job_callback(context) -> None:
    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(tick_async(), loop)


def setup_cashout_send_notify_job(app) -> None:
    """Poll the queue on the bot worker (the only process that owns the listener)."""
    from club_gc_settings import is_dm_gc_listener_enabled

    if not is_dm_gc_listener_enabled():
        return
    for job in app.job_queue.get_jobs_by_name(JOB_NAME):
        job.schedule_removal()
    app.job_queue.run_repeating(
        cashout_send_notify_job_callback,
        interval=timedelta(seconds=TICK_INTERVAL_SEC),
        first=timedelta(seconds=TICK_INTERVAL_SEC),
        name=JOB_NAME,
    )
    logger.info("cashout_send_notify job scheduled interval_sec=%s", TICK_INTERVAL_SEC)
