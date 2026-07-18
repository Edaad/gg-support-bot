"""Segment + classify complete daily transcripts into group_chat_tickets."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from club_gc_settings import get_club_gc_config_by_link_club_id, get_gc_users_to_add
from db.connection import get_db
from db.models import Group, GroupChatDailyTranscript, GroupChatTicket
from bot.services.group_chat_analysis_claude import (
    classify_ticket,
    get_anthropic_model,
    segment_messages,
)
from bot.services.group_chat_analysis_prompts import PROMPT_VERSION
from bot.services.group_chat_transcript_fetch import STATUS_COMPLETE as TRANSCRIPT_COMPLETE

logger = logging.getLogger(__name__)

ANALYSIS_PENDING = "pending"
ANALYSIS_COMPLETE = "complete"
ANALYSIS_FAILED = "failed"

_KNOWN_TRANSLATION_BOTS = ("YTranslateBot", "@YTranslateBot")
_BUDGET_SECONDS = 30 * 60


def default_analysis_concurrency() -> int:
    """Max concurrent chat analyses. ``0`` = unlimited (all chats in parallel)."""

    raw = (os.getenv("GROUP_CHAT_ANALYSIS_CONCURRENCY") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@dataclass(frozen=True)
class AnalysisChatTarget:
    activity_date: date
    chat_id: int
    club_id: int


@dataclass
class AnalysisChatResult:
    chat_id: int
    club_id: int
    status: str
    ticket_count: int = 0
    error: str | None = None


@dataclass
class AnalysisRunSummary:
    activity_date: date
    complete: int = 0
    failed: int = 0
    timed_out: int = 0
    results: list[AnalysisChatResult] = field(default_factory=list)


def _norm_handle(raw: str | None) -> str:
    text = (raw or "").strip()
    if text.startswith("@"):
        text = text[1:]
    return text


def role_lists_for_club(club_id: int) -> tuple[list[str], list[str]]:
    """Return (admin_account_names, bot_names) for classification prompts."""

    admins: list[str] = []
    bots: list[str] = list(_KNOWN_TRANSLATION_BOTS)
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is not None:
        for handle in get_gc_users_to_add(cfg):
            cleaned = _norm_handle(str(handle))
            if cleaned:
                admins.append(cleaned)
                admins.append(f"@{cleaned}")
        if cfg.bot_account:
            cleaned = _norm_handle(cfg.bot_account)
            if cleaned:
                bots.append(cleaned)
                bots.append(f"@{cleaned}")
    # Stable unique order
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    return _uniq(admins), _uniq(bots)


def chat_display_name(chat_id: int) -> str:
    with get_db() as session:
        row = session.query(Group).filter(Group.chat_id == int(chat_id)).one_or_none()
        if row is not None and (row.name or "").strip():
            return str(row.name).strip()
    return f"chat {chat_id}"


def list_analysis_targets(
    activity_date: date,
    *,
    chat_id: int | None = None,
    club_id: int | None = None,
    force: bool = False,
) -> list[AnalysisChatTarget]:
    """List complete transcripts to analyze.

    By default skips ``analysis_status=complete``. Pass ``force=True`` to
    re-analyze (ticket rows are replaced on success — upsert per chat-day).
    """

    with get_db() as session:
        q = session.query(GroupChatDailyTranscript).filter(
            GroupChatDailyTranscript.activity_date == activity_date,
            GroupChatDailyTranscript.status == TRANSCRIPT_COMPLETE,
        )
        if not force:
            q = q.filter(
                GroupChatDailyTranscript.analysis_status != ANALYSIS_COMPLETE
            )
        if chat_id is not None:
            q = q.filter(GroupChatDailyTranscript.chat_id == int(chat_id))
        if club_id is not None:
            q = q.filter(GroupChatDailyTranscript.club_id == int(club_id))
        rows = q.order_by(
            GroupChatDailyTranscript.club_id,
            GroupChatDailyTranscript.chat_id,
        ).all()
        return [
            AnalysisChatTarget(
                activity_date=r.activity_date,
                chat_id=int(r.chat_id),
                club_id=int(r.club_id),
            )
            for r in rows
        ]


def _mark_analysis_attempt_start(
    *,
    activity_date: date,
    chat_id: int,
) -> None:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == activity_date,
                GroupChatDailyTranscript.chat_id == int(chat_id),
            )
            .one_or_none()
        )
        if row is None:
            return
        row.analysis_attempt_count = int(row.analysis_attempt_count or 0) + 1
        row.analysis_status = ANALYSIS_PENDING
        row.analysis_error = None
        session.commit()


def _mark_analysis_complete(
    *,
    activity_date: date,
    chat_id: int,
) -> None:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == activity_date,
                GroupChatDailyTranscript.chat_id == int(chat_id),
            )
            .one_or_none()
        )
        if row is None:
            return
        row.analysis_status = ANALYSIS_COMPLETE
        row.analysis_error = None
        row.analyzed_at = datetime.now(timezone.utc)
        session.commit()


def _mark_analysis_failed(
    *,
    activity_date: date,
    chat_id: int,
    error: str,
) -> None:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == activity_date,
                GroupChatDailyTranscript.chat_id == int(chat_id),
            )
            .one_or_none()
        )
        if row is None:
            return
        row.analysis_status = ANALYSIS_FAILED
        row.analysis_error = (error or "")[:4000]
        session.commit()


def _load_transcript_messages(
    *,
    activity_date: date,
    chat_id: int,
) -> tuple[int, list[dict[str, Any]]]:
    with get_db() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == activity_date,
                GroupChatDailyTranscript.chat_id == int(chat_id),
            )
            .one_or_none()
        )
        if row is None:
            raise ValueError(f"transcript not found chat_id={chat_id} date={activity_date}")
        if row.status != TRANSCRIPT_COMPLETE:
            raise ValueError(f"transcript not complete status={row.status}")
        messages = row.messages if isinstance(row.messages, list) else []
        return int(row.club_id), [m for m in messages if isinstance(m, dict)]


def _slice_messages(
    messages: list[dict[str, Any]],
    message_ids: list[int],
) -> list[dict[str, Any]]:
    wanted = {int(x) for x in message_ids}
    return [m for m in messages if int(m.get("id") or 0) in wanted]


def _replace_tickets(
    *,
    activity_date: date,
    chat_id: int,
    club_id: int,
    tickets: list[dict[str, Any]],
    model: str,
) -> int:
    with get_db() as session:
        (
            session.query(GroupChatTicket)
            .filter(
                GroupChatTicket.activity_date == activity_date,
                GroupChatTicket.chat_id == int(chat_id),
            )
            .delete(synchronize_session=False)
        )
        for ticket in tickets:
            session.add(
                GroupChatTicket(
                    activity_date=activity_date,
                    chat_id=int(chat_id),
                    club_id=int(club_id),
                    ticket_index=int(ticket["ticket_index"]),
                    start_msg_id=int(ticket["start_msg_id"]),
                    end_msg_id=int(ticket["end_msg_id"]),
                    message_ids=list(ticket["message_ids"]),
                    brief_summary=ticket.get("brief_summary"),
                    category=str(ticket["category"]),
                    events=ticket.get("events"),
                    summary=ticket.get("summary"),
                    prompt_version=PROMPT_VERSION,
                    model=model,
                )
            )
        session.commit()
    return len(tickets)


async def analyze_transcript_for_chat(
    *,
    activity_date: date,
    chat_id: int,
    club_id: int | None = None,
) -> AnalysisChatResult:
    """Segment + classify one transcript. Only replaces tickets on full success."""

    _mark_analysis_attempt_start(activity_date=activity_date, chat_id=chat_id)
    try:
        resolved_club_id, messages = _load_transcript_messages(
            activity_date=activity_date,
            chat_id=chat_id,
        )
        if club_id is not None and int(club_id) != resolved_club_id:
            raise ValueError(
                f"club_id mismatch expected={club_id} transcript={resolved_club_id}"
            )
        club_id = resolved_club_id
        if not messages:
            # Empty day still counts as analyzed (zero tickets).
            _replace_tickets(
                activity_date=activity_date,
                chat_id=chat_id,
                club_id=club_id,
                tickets=[],
                model=get_anthropic_model(),
            )
            _mark_analysis_complete(activity_date=activity_date, chat_id=chat_id)
            return AnalysisChatResult(
                chat_id=chat_id,
                club_id=club_id,
                status=ANALYSIS_COMPLETE,
                ticket_count=0,
            )

        name = chat_display_name(chat_id)
        admin_names, bot_names = role_lists_for_club(club_id)
        segmented = await segment_messages(chat_name=name, messages=messages)
        model = get_anthropic_model()
        classified_rows: list[dict[str, Any]] = []
        for ticket in segmented["tickets"]:
            slice_msgs = _slice_messages(messages, ticket["message_ids"])
            if not slice_msgs:
                raise ValueError(
                    f"ticket {ticket['ticket_index']} message_ids not found in transcript"
                )
            classification = await classify_ticket(
                chat_name=name,
                messages=slice_msgs,
                admin_names=admin_names,
                bot_names=bot_names,
            )
            classified_rows.append(
                {
                    **ticket,
                    "category": classification["category"],
                    "events": classification["events"],
                    "summary": classification["summary"],
                }
            )

        count = _replace_tickets(
            activity_date=activity_date,
            chat_id=chat_id,
            club_id=club_id,
            tickets=classified_rows,
            model=model,
        )
        _mark_analysis_complete(activity_date=activity_date, chat_id=chat_id)
        return AnalysisChatResult(
            chat_id=chat_id,
            club_id=club_id,
            status=ANALYSIS_COMPLETE,
            ticket_count=count,
        )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "group_chat_analysis: failed chat_id=%s date=%s err=%s",
            chat_id,
            activity_date,
            err,
            exc_info=True,
        )
        _mark_analysis_failed(
            activity_date=activity_date,
            chat_id=chat_id,
            error=err,
        )
        return AnalysisChatResult(
            chat_id=chat_id,
            club_id=int(club_id or 0),
            status=ANALYSIS_FAILED,
            error=err,
        )


async def analyze_with_retries(
    activity_date: date,
    *,
    chat_id: int | None = None,
    club_id: int | None = None,
    budget_seconds: float = _BUDGET_SECONDS,
    force: bool = False,
    concurrency: int | None = None,
) -> AnalysisRunSummary:
    """Analyze transcripts in parallel; retry failures within the time budget.

    ``force=True`` re-analyzes chats already marked complete and replaces their
    ticket rows on success (manual day upsert).

    ``concurrency`` caps parallel chats (``0`` / default = all at once). Override
    via ``GROUP_CHAT_ANALYSIS_CONCURRENCY``.
    """

    limit = default_analysis_concurrency() if concurrency is None else max(0, int(concurrency))
    deadline = time.monotonic() + float(budget_seconds)
    summary = AnalysisRunSummary(activity_date=activity_date)
    pending = list_analysis_targets(
        activity_date, chat_id=chat_id, club_id=club_id, force=force
    )
    if not pending:
        return summary

    async def _run_batch(targets: list[AnalysisChatTarget]) -> list[AnalysisChatResult]:
        if not targets:
            return []
        sem = asyncio.Semaphore(limit) if limit > 0 else None

        async def _one(t: AnalysisChatTarget) -> AnalysisChatResult:
            if time.monotonic() >= deadline:
                return AnalysisChatResult(
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                    status=ANALYSIS_FAILED,
                    error="timed out before analysis",
                )
            if sem is None:
                return await analyze_transcript_for_chat(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                )
            async with sem:
                if time.monotonic() >= deadline:
                    return AnalysisChatResult(
                        chat_id=t.chat_id,
                        club_id=t.club_id,
                        status=ANALYSIS_FAILED,
                        error="timed out before analysis",
                    )
                return await analyze_transcript_for_chat(
                    activity_date=t.activity_date,
                    chat_id=t.chat_id,
                    club_id=t.club_id,
                )

        return list(await asyncio.gather(*[_one(t) for t in targets]))

    logger.info(
        "group_chat_analysis: starting activity_date=%s chats=%s concurrency=%s force=%s",
        activity_date,
        len(pending),
        "unlimited" if limit == 0 else limit,
        force,
    )

    first_pass = await _run_batch(pending)
    failed = [r for r in first_pass if r.status != ANALYSIS_COMPLETE]
    complete = [r for r in first_pass if r.status == ANALYSIS_COMPLETE]
    timed_out_results = [r for r in failed if r.error == "timed out before analysis"]
    failed = [r for r in failed if r.error != "timed out before analysis"]

    # Retry failures while budget remains (never force on retry list — only failed).
    while failed and time.monotonic() < deadline:
        retry_targets = [
            AnalysisChatTarget(
                activity_date=activity_date,
                chat_id=r.chat_id,
                club_id=r.club_id,
            )
            for r in failed
        ]
        still_open = {
            (t.chat_id, t.club_id)
            for t in list_analysis_targets(
                activity_date, chat_id=chat_id, club_id=club_id, force=False
            )
        }
        retry_targets = [
            t for t in retry_targets if (t.chat_id, t.club_id) in still_open
        ]
        if not retry_targets:
            break
        retry_results = await _run_batch(retry_targets)
        newly_complete = [r for r in retry_results if r.status == ANALYSIS_COMPLETE]
        still_failed = [
            r
            for r in retry_results
            if r.status != ANALYSIS_COMPLETE and r.error != "timed out before analysis"
        ]
        timed_out_results.extend(
            r
            for r in retry_results
            if r.status != ANALYSIS_COMPLETE and r.error == "timed out before analysis"
        )
        complete.extend(newly_complete)
        failed = still_failed
        if not newly_complete:
            # No progress — stop to avoid tight loop on permanent errors.
            break

    summary.complete = len(complete)
    summary.failed = len(failed)
    summary.timed_out = len(timed_out_results)
    summary.results = complete + failed + timed_out_results
    logger.info(
        "group_chat_analysis: done activity_date=%s force=%s complete=%s failed=%s timed_out=%s",
        activity_date,
        force,
        summary.complete,
        summary.failed,
        summary.timed_out,
    )
    return summary
