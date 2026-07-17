"""Fetch and store previous-day support-group transcripts via MTProto."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

from telethon.errors import FloodWaitError
from telethon.tl.types import MessageService

from bot.services.club import EST
from bot.services.mtproto_group_create import (
    FLOODWAIT_MAX_SECONDS,
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)
from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from db.connection import get_db
from db.models import GroupChatDailyActivity, GroupChatDailyTranscript

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ActivityChatTarget:
    activity_date: date
    chat_id: int
    club_id: int


@dataclass
class ChatFetchResult:
    chat_id: int
    club_id: int
    status: str
    message_count: int = 0
    error: str | None = None


@dataclass
class TranscriptRunSummary:
    activity_date: date
    complete: int = 0
    failed: int = 0
    timed_out: int = 0
    results: list[ChatFetchResult] = field(default_factory=list)


def et_day_window_utc(activity_date: date) -> tuple[datetime, datetime]:
    """Return [start, end) for an America/New_York calendar day, as UTC datetimes."""

    start_et = datetime.combine(activity_date, dt_time.min, tzinfo=EST)
    end_et = start_et + timedelta(days=1)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def previous_et_activity_date(now: datetime | None = None) -> date:
    """Previous complete America/New_York calendar day (T+1 extract target)."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(EST).date() - timedelta(days=1))


def _message_date_utc(msg_date: datetime) -> datetime:
    if msg_date.tzinfo is None:
        return msg_date.replace(tzinfo=timezone.utc)
    return msg_date.astimezone(timezone.utc)


def _media_type(msg: Any) -> str | None:
    media = getattr(msg, "media", None)
    if media is None:
        return None
    return type(media).__name__


def _media_filename(msg: Any) -> str | None:
    media = getattr(msg, "media", None)
    if media is None:
        return None
    document = getattr(media, "document", None)
    if document is None:
        return None
    for attr in getattr(document, "attributes", None) or ():
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return None


def _sender_fields(msg: Any) -> tuple[int | None, str | None, str | None, bool]:
    sender_id = getattr(msg, "sender_id", None)
    if sender_id is not None:
        sender_id = int(sender_id)

    sender = getattr(msg, "sender", None)
    sender_name: str | None = None
    username: str | None = None
    is_bot = False
    if sender is not None:
        first = (getattr(sender, "first_name", None) or "").strip()
        last = (getattr(sender, "last_name", None) or "").strip()
        title = (getattr(sender, "title", None) or "").strip()
        combined = f"{first} {last}".strip()
        sender_name = combined or title or None
        raw_username = getattr(sender, "username", None)
        username = str(raw_username) if raw_username else None
        is_bot = bool(getattr(sender, "bot", False))

    return sender_id, sender_name, username, is_bot


def serialize_telethon_message(msg: Any) -> dict[str, Any]:
    """Serialize a Telethon message to a JSON-safe dict (media metadata only)."""

    is_service = isinstance(msg, MessageService) or bool(getattr(msg, "action", None))
    text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
    if is_service and not text:
        action = getattr(msg, "action", None)
        text = type(action).__name__ if action is not None else ""

    date_utc = _message_date_utc(msg.date)
    edit_date = getattr(msg, "edit_date", None)
    edit_iso = None
    if edit_date is not None:
        edit_iso = _message_date_utc(edit_date).isoformat()

    reply_to = getattr(msg, "reply_to", None)
    reply_to_msg_id = None
    if reply_to is not None:
        reply_id = getattr(reply_to, "reply_to_msg_id", None)
        if reply_id is not None:
            reply_to_msg_id = int(reply_id)

    sender_id, sender_name, username, is_bot = _sender_fields(msg)

    return {
        "id": int(msg.id),
        "date": date_utc.isoformat(),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "username": username,
        "is_bot": is_bot,
        "text": text,
        "reply_to_msg_id": reply_to_msg_id,
        "media_type": _media_type(msg),
        "media_filename": _media_filename(msg),
        "edit_date": edit_iso,
        "is_service": is_service,
    }


def list_activity_targets(
    activity_date: date,
    *,
    chat_id: int | None = None,
    club_id: int | None = None,
) -> list[ActivityChatTarget]:
    with get_db() as session:
        q = session.query(GroupChatDailyActivity).filter(
            GroupChatDailyActivity.activity_date == activity_date
        )
        if chat_id is not None:
            q = q.filter(GroupChatDailyActivity.chat_id == int(chat_id))
        if club_id is not None:
            q = q.filter(GroupChatDailyActivity.club_id == int(club_id))
        rows = q.order_by(
            GroupChatDailyActivity.club_id,
            GroupChatDailyActivity.chat_id,
        ).all()
        return [
            ActivityChatTarget(
                activity_date=row.activity_date,
                chat_id=int(row.chat_id),
                club_id=int(row.club_id),
            )
            for row in rows
        ]


def _mark_attempt_start(
    *,
    activity_date: date,
    chat_id: int,
    club_id: int,
) -> None:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter_by(activity_date=activity_date, chat_id=int(chat_id))
            .one_or_none()
        )
        if row is None:
            session.add(
                GroupChatDailyTranscript(
                    activity_date=activity_date,
                    chat_id=int(chat_id),
                    club_id=int(club_id),
                    status=STATUS_PENDING,
                    message_count=0,
                    messages=None,
                    error=None,
                    attempt_count=1,
                )
            )
            return
        row.club_id = int(club_id)
        row.status = STATUS_PENDING
        row.error = None
        row.attempt_count = int(row.attempt_count or 0) + 1


def _mark_attempt_complete(
    *,
    activity_date: date,
    chat_id: int,
    club_id: int,
    messages: list[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter_by(activity_date=activity_date, chat_id=int(chat_id))
            .one_or_none()
        )
        if row is None:
            session.add(
                GroupChatDailyTranscript(
                    activity_date=activity_date,
                    chat_id=int(chat_id),
                    club_id=int(club_id),
                    status=STATUS_COMPLETE,
                    message_count=len(messages),
                    messages=messages,
                    error=None,
                    attempt_count=1,
                    fetched_at=now,
                )
            )
            return
        row.club_id = int(club_id)
        row.status = STATUS_COMPLETE
        row.message_count = len(messages)
        row.messages = messages
        row.error = None
        row.fetched_at = now


def _mark_attempt_failed(
    *,
    activity_date: date,
    chat_id: int,
    club_id: int,
    error: str,
) -> None:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter_by(activity_date=activity_date, chat_id=int(chat_id))
            .one_or_none()
        )
        err = (error or "unknown error")[:2000]
        if row is None:
            session.add(
                GroupChatDailyTranscript(
                    activity_date=activity_date,
                    chat_id=int(chat_id),
                    club_id=int(club_id),
                    status=STATUS_FAILED,
                    message_count=0,
                    messages=None,
                    error=err,
                    attempt_count=1,
                )
            )
            return
        row.club_id = int(club_id)
        row.status = STATUS_FAILED
        row.error = err


async def _collect_day_messages_once(
    client: Any,
    chat_id: int,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    async for msg in client.iter_messages(int(chat_id), offset_date=end_utc):
        msg_date = _message_date_utc(msg.date)
        if msg_date < start_utc:
            break
        if msg_date >= end_utc:
            continue
        collected.append(serialize_telethon_message(msg))
    collected.reverse()
    return collected


async def _iter_day_messages(
    client: Any,
    chat_id: int,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    """Walk newest→oldest from end_utc until before start_utc; return chronological."""

    try:
        return await _collect_day_messages_once(
            client, chat_id, start_utc=start_utc, end_utc=end_utc
        )
    except FloodWaitError as e:
        if e.seconds > FLOODWAIT_MAX_SECONDS:
            raise RuntimeError(
                f"Telegram FloodWait too long ({e.seconds}s) for chat {chat_id}"
            ) from e
        logger.info(
            "group_transcript: FloodWait %ss chat_id=%s; sleeping once",
            e.seconds,
            chat_id,
        )
        await asyncio.sleep(float(e.seconds) + 1.0)
        return await _collect_day_messages_once(
            client, chat_id, start_utc=start_utc, end_utc=end_utc
        )


async def fetch_transcript_for_chat(
    cfg: ClubGcConfig,
    *,
    chat_id: int,
    club_id: int,
    activity_date: date,
    client: Any | None = None,
) -> ChatFetchResult:
    """Fetch one chat's day window and upsert the transcript row."""

    _mark_attempt_start(
        activity_date=activity_date,
        chat_id=chat_id,
        club_id=club_id,
    )
    start_utc, end_utc = et_day_window_utc(activity_date)

    owns_client = client is None
    try:
        if owns_client:
            if not await is_client_authorized(cfg):
                raise RuntimeError(
                    f"MTProto session not authorized for club={cfg.club_key}"
                )
            async with get_mtproto_lock(cfg.club_key):
                client = make_client(cfg)
                await client.connect()
                try:
                    if not await client.is_user_authorized():
                        raise RuntimeError(
                            f"MTProto session not authorized after connect "
                            f"club={cfg.club_key}"
                        )
                    messages = await _iter_day_messages(
                        client,
                        chat_id,
                        start_utc=start_utc,
                        end_utc=end_utc,
                    )
                finally:
                    await client.disconnect()
        else:
            messages = await _iter_day_messages(
                client,
                chat_id,
                start_utc=start_utc,
                end_utc=end_utc,
            )

        _mark_attempt_complete(
            activity_date=activity_date,
            chat_id=chat_id,
            club_id=club_id,
            messages=messages,
        )
        return ChatFetchResult(
            chat_id=chat_id,
            club_id=club_id,
            status=STATUS_COMPLETE,
            message_count=len(messages),
        )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "group_transcript: fetch failed chat_id=%s club_id=%s date=%s err=%s",
            chat_id,
            club_id,
            activity_date,
            err,
            exc_info=True,
        )
        _mark_attempt_failed(
            activity_date=activity_date,
            chat_id=chat_id,
            club_id=club_id,
            error=err,
        )
        return ChatFetchResult(
            chat_id=chat_id,
            club_id=club_id,
            status=STATUS_FAILED,
            error=err,
        )


async def _fetch_club_chats(
    cfg: ClubGcConfig,
    targets: list[ActivityChatTarget],
    *,
    deadline_monotonic: float | None,
) -> list[ChatFetchResult]:
    results: list[ChatFetchResult] = []
    async with get_mtproto_lock(cfg.club_key):
        if not await is_client_authorized(cfg):
            err = f"MTProto session not authorized for club={cfg.club_key}"
            for t in targets:
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    results.append(
                        ChatFetchResult(
                            chat_id=t.chat_id,
                            club_id=t.club_id,
                            status=STATUS_FAILED,
                            error="timed out before fetch",
                        )
                    )
                    continue
                _mark_attempt_start(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                )
                _mark_attempt_failed(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                    error=err,
                )
                results.append(
                    ChatFetchResult(
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                        status=STATUS_FAILED,
                        error=err,
                    )
                )
            return results

        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                err = (
                    f"MTProto session not authorized after connect "
                    f"club={cfg.club_key}"
                )
                for t in targets:
                    _mark_attempt_start(
                        activity_date=t.activity_date,
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                    )
                    _mark_attempt_failed(
                        activity_date=t.activity_date,
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                        error=err,
                    )
                    results.append(
                        ChatFetchResult(
                            chat_id=t.chat_id,
                            club_id=t.club_id,
                            status=STATUS_FAILED,
                            error=err,
                        )
                    )
                return results

            for t in targets:
                if (
                    deadline_monotonic is not None
                    and time.monotonic() >= deadline_monotonic
                ):
                    err = "timed out before fetch"
                    _mark_attempt_start(
                        activity_date=t.activity_date,
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                    )
                    _mark_attempt_failed(
                        activity_date=t.activity_date,
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                        error=err,
                    )
                    results.append(
                        ChatFetchResult(
                            chat_id=t.chat_id,
                            club_id=t.club_id,
                            status=STATUS_FAILED,
                            error=err,
                        )
                    )
                    continue
                result = await fetch_transcript_for_chat(
                    cfg,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                    activity_date=t.activity_date,
                    client=client,
                )
                results.append(result)
        finally:
            await client.disconnect()
    return results


async def fetch_transcripts_for_activity_date(
    activity_date: date,
    *,
    chat_id: int | None = None,
    club_id: int | None = None,
    deadline_monotonic: float | None = None,
) -> TranscriptRunSummary:
    """Fetch transcripts for all (or filtered) active chats on ``activity_date``.

    Clubs run in parallel; chats within a club are sequential. Optional
    ``chat_id`` / ``club_id`` support one-group validation before enabling the cron.
    """

    targets = list_activity_targets(
        activity_date, chat_id=chat_id, club_id=club_id
    )
    summary = TranscriptRunSummary(activity_date=activity_date)
    if not targets:
        return summary

    by_club: dict[int, list[ActivityChatTarget]] = {}
    for t in targets:
        by_club.setdefault(t.club_id, []).append(t)

    async def _club_task(
        club_db_id: int, club_targets: list[ActivityChatTarget]
    ) -> list[ChatFetchResult]:
        cfg = get_club_gc_config_by_link_club_id(club_db_id)
        if cfg is None:
            err = f"No ClubGcConfig for club_id={club_db_id}"
            out: list[ChatFetchResult] = []
            for t in club_targets:
                _mark_attempt_start(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                )
                _mark_attempt_failed(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                    error=err,
                )
                out.append(
                    ChatFetchResult(
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                        status=STATUS_FAILED,
                        error=err,
                    )
                )
            return out
        return await _fetch_club_chats(
            cfg,
            club_targets,
            deadline_monotonic=deadline_monotonic,
        )

    club_results = await asyncio.gather(
        *[_club_task(cid, rows) for cid, rows in by_club.items()]
    )
    for batch in club_results:
        for result in batch:
            summary.results.append(result)
            if result.status == STATUS_COMPLETE:
                summary.complete += 1
            else:
                if (result.error or "").startswith("timed out"):
                    summary.timed_out += 1
                summary.failed += 1
    return summary


def _recount_summary(summary: TranscriptRunSummary) -> None:
    summary.complete = sum(
        1 for r in summary.results if r.status == STATUS_COMPLETE
    )
    summary.failed = sum(
        1 for r in summary.results if r.status != STATUS_COMPLETE
    )
    summary.timed_out = sum(
        1
        for r in summary.results
        if r.status != STATUS_COMPLETE
        and (r.error or "").startswith("timed out")
    )


async def fetch_with_retries(
    activity_date: date,
    *,
    chat_id: int | None = None,
    club_id: int | None = None,
    budget_seconds: float = 30 * 60,
) -> TranscriptRunSummary:
    """Fetch all targets, then retry failed chats while time remains."""

    deadline = time.monotonic() + max(0.0, budget_seconds)
    summary = await fetch_transcripts_for_activity_date(
        activity_date,
        chat_id=chat_id,
        club_id=club_id,
        deadline_monotonic=deadline,
    )
    _recount_summary(summary)

    while time.monotonic() < deadline:
        failed = [
            r
            for r in summary.results
            if r.status != STATUS_COMPLETE
            and not (r.error or "").startswith("timed out")
        ]
        if not failed:
            break

        by_chat = {r.chat_id: r for r in summary.results}
        for fr in failed:
            if time.monotonic() >= deadline:
                err = "timed out before fetch"
                _mark_attempt_start(
                    activity_date=activity_date,
                    chat_id=fr.chat_id,
                    club_id=fr.club_id,
                )
                _mark_attempt_failed(
                    activity_date=activity_date,
                    chat_id=fr.chat_id,
                    club_id=fr.club_id,
                    error=err,
                )
                by_chat[fr.chat_id] = ChatFetchResult(
                    chat_id=fr.chat_id,
                    club_id=fr.club_id,
                    status=STATUS_FAILED,
                    error=err,
                )
                continue
            one = await fetch_transcripts_for_activity_date(
                activity_date,
                chat_id=fr.chat_id,
                club_id=fr.club_id,
                deadline_monotonic=deadline,
            )
            if one.results:
                by_chat[fr.chat_id] = one.results[0]

        summary.results = list(by_chat.values())
        _recount_summary(summary)

    return summary
