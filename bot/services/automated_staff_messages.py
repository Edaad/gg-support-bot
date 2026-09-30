"""Record messages the bot sends through a club MTProto (staff) session.

The club account is also a human staff account, so the response audit needs to
know which of its messages were automated. Every MTProto ``send_message`` /
``send_file`` call site passes its Telethon result to :func:`record_sent`.

Kinds in :data:`STAFF_ACTION_KINDS` are confirmations of a staff command that the
bot deleted and replaced (``/add``, ``/bonus``, ``/lmk``, ``/cash``). They are
recorded for completeness, but the audit counts them as the staff reply because
the staff member's own command no longer exists in the chat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.connection import get_db
from db.models import AutomatedStaffMessage

logger = logging.getLogger(__name__)

# Pure automation: never a staff reply.
KIND_CASH_OWED_PIN = "cash_owed_pin"
KIND_CASH_ASAP = "cash_asap"
KIND_GC_INVITE_MESSAGE = "gc_invite_message"
KIND_PLAYER_DM_REDIRECT = "player_dm_redirect"
KIND_STAFF_GC_CONFIRMATION = "staff_gc_confirmation"
KIND_AUTO_ADD_CONFIRMATION = "auto_add_confirmation"
KIND_CASHOUT_SEND_PROOF = "cashout_send_proof"
KIND_OUTREACH_DM = "outreach_dm"
KIND_ADMIN_DM = "admin_dm"

# Echo of a staff command the bot deleted: counts as the staff reply.
KIND_STAFF_ADD_CONFIRMATION = "staff_add_confirmation"
KIND_STAFF_BONUS_CONFIRMATION = "staff_bonus_confirmation"
KIND_STAFF_LMK = "staff_lmk"
KIND_STAFF_CASH_WORKING = "staff_cash_working"

STAFF_ACTION_KINDS = frozenset(
    {
        KIND_STAFF_ADD_CONFIRMATION,
        KIND_STAFF_BONUS_CONFIRMATION,
        KIND_STAFF_LMK,
        KIND_STAFF_CASH_WORKING,
    }
)


def _message_refs(sent: Any) -> list[tuple[int, int]]:
    """``(chat_id, message_id)`` for a Telethon send result (message or album list)."""

    items = sent if isinstance(sent, (list, tuple)) else [sent]
    refs: list[tuple[int, int]] = []
    for msg in items:
        chat_id = getattr(msg, "chat_id", None)
        msg_id = getattr(msg, "id", None)
        if chat_id is None or msg_id is None:
            continue
        try:
            refs.append((int(chat_id), int(msg_id)))
        except (TypeError, ValueError):
            continue
    return refs


def record_automated_staff_messages(refs: Iterable[tuple[int, int]], kind: str) -> None:
    """Insert rows; duplicates are ignored. Never raises."""

    rows = [
        {"chat_id": int(c), "message_id": int(m), "kind": str(kind)} for c, m in refs
    ]
    if not rows:
        return
    try:
        with get_db() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                stmt = pg_insert(AutomatedStaffMessage).values(rows)
                session.execute(
                    stmt.on_conflict_do_nothing(
                        index_elements=["chat_id", "message_id"]
                    )
                )
                return
            for row in rows:
                exists = session.execute(
                    select(AutomatedStaffMessage.id).where(
                        AutomatedStaffMessage.chat_id == row["chat_id"],
                        AutomatedStaffMessage.message_id == row["message_id"],
                    )
                ).first()
                if exists is None:
                    session.add(AutomatedStaffMessage(**row))
    except Exception:
        logger.warning(
            "automated_staff_messages: record failed kind=%s refs=%s",
            kind,
            rows,
            exc_info=True,
        )


async def record_sent(sent: Any, *, kind: str) -> None:
    """Record a Telethon send result off the event loop. Never raises."""

    refs = _message_refs(sent)
    if not refs:
        return
    try:
        await asyncio.to_thread(record_automated_staff_messages, refs, kind)
    except Exception:
        logger.warning(
            "automated_staff_messages: record_sent failed kind=%s", kind, exc_info=True
        )


def load_automated_message_kinds(chat_id: int) -> dict[int, str]:
    """``message_id → kind`` for one chat."""

    with get_db() as session:
        rows = session.execute(
            select(AutomatedStaffMessage.message_id, AutomatedStaffMessage.kind).where(
                AutomatedStaffMessage.chat_id == int(chat_id)
            )
        ).all()
    return {int(mid): str(kind) for mid, kind in rows}
