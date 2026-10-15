"""Analytics tools: KPIs, deposit funnel, daily group-chat activity, tickets, transcripts."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer

from api.group_chat_ticket_helpers import slice_ticket_messages
from api.kpis import compute_kpis
from api.routes.deposit_funnel import (
    deposit_funnel_latency_summary as _route_latency_summary,
)
from api.routes.deposit_funnel import deposit_funnel_summary as _route_funnel_summary
from api.routes.group_chat_activity import _enrich_tickets
from db.models import (
    Group,
    GroupChatDailyActivity,
    GroupChatDailyTranscript,
    GroupChatTicket,
)
from mcp_server.dates import parse_et_day, parse_range, today_et
from mcp_server.db import read_session
from mcp_server.envelope import page, single
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club,
)

_MAX_TRANSCRIPT_MESSAGES = 1000
_ET_DAY_DOC = (
    "activity_date is a YYYY-MM-DD America/New_York day (default: yesterday ET)."
)


def _yesterday_et() -> date:
    return today_et() - timedelta(days=1)


def _ticket_categories() -> tuple[str, ...]:
    from bot.services.group_chat_analysis_prompts import TICKET_CATEGORIES

    return TICKET_CATEGORIES


def _validate_category(category: str | None) -> str | None:
    if category is None or not category.strip():
        return None
    cat = category.strip().lower()
    allowed = _ticket_categories()
    if cat not in allowed:
        raise bad_input(f"category must be one of: {', '.join(allowed)}")
    return cat


def _group_titles(session: Any, chat_ids: set[int]) -> dict[int, str | None]:
    if not chat_ids:
        return {}
    return {
        int(g.chat_id): (str(g.name).strip() if g.name else None)
        for g in session.query(Group.chat_id, Group.name)
        .filter(Group.chat_id.in_(chat_ids))
        .all()
    }


def _transcript_message(src: Any) -> dict[str, Any]:
    """Map a stored transcript message to a compact dict with PII under masked keys."""
    if not isinstance(src, dict):
        return {"raw": src}
    out = dict(src)
    out["display_name"] = out.pop("sender_name", None)
    return out


@translate_errors
def deposit_funnel_summary(
    club: str | None = None,
    method: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    is_first_deposit: bool | None = None,
    requires_method_setup: bool | None = None,
    exclude_test_chats: bool = True,
    include_latency: bool = False,
) -> dict[str, Any]:
    """Deposit funnel (/deposit started -> chips confirmed): per-step session counts,
    conversion vs started and average seconds between steps, like the dashboard
    Deposit Funnel card. `method` is a payment-method slug; from/to are UTC
    datetimes (YYYY-MM-DD = that America/New_York day). `include_latency` adds the
    full-auto e2e latency funnel (sessions with no bind setup). Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    start, stop = parse_range(from_, to)
    from_text = start.isoformat() if start else None
    to_text = stop.isoformat() if stop else None
    with read_session() as session:
        club_row = resolve_club(session, club)
        club_id = int(club_row.id) if club_row is not None else None
        summary = _route_funnel_summary(
            club_id=club_id,
            method=method,
            is_first_deposit=is_first_deposit,
            requires_method_setup=requires_method_setup,
            from_dt=from_text,
            to_dt=to_text,
            exclude_test_chats=exclude_test_chats,
            db=session,
        ).model_dump()
        summary["club_name"] = str(club_row.name) if club_row is not None else None
        latency = None
        if include_latency:
            latency = _route_latency_summary(
                club_id=club_id,
                method=method,
                from_dt=from_text,
                to_dt=to_text,
                exclude_test_chats=exclude_test_chats,
                db=session,
            ).model_dump()
    notes = ["counts are distinct deposit sessions", "latencies are seconds"]
    if exclude_test_chats:
        notes.append("excludes test chats by default")
    if not include_latency:
        notes.append("latency omitted; pass include_latency=true for the e2e funnel")
    return single({"summary": summary, "latency": latency}, notes=notes)


@translate_errors
def list_group_chat_activity(
    activity_date: str | None = None,
    club: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Daily rollup of non-bot messages per support group (which player chats were
    active, how many messages, first/last message time). Rows carry chat_id and
    group_title for use with list_tickets / get_chat_transcript. activity_date is a YYYY-MM-DD America/New_York day (default: yesterday ET). Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    day = parse_et_day(activity_date, default=_yesterday_et())
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_row = resolve_club(session, club)
        query = session.query(GroupChatDailyActivity).filter(
            GroupChatDailyActivity.activity_date == day
        )
        if club_row is not None:
            query = query.filter(GroupChatDailyActivity.club_id == club_row.id)
        total = query.order_by(None).count()
        rows = (
            query.order_by(
                GroupChatDailyActivity.non_bot_message_count.desc(),
                GroupChatDailyActivity.club_id,
                GroupChatDailyActivity.chat_id,
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        titles = _group_titles(session, {int(r.chat_id) for r in rows})
        items = [
            {
                "id": int(r.id),
                "activity_date": r.activity_date,
                "chat_id": int(r.chat_id),
                "group_title": titles.get(int(r.chat_id)),
                "club_id": int(r.club_id),
                "club_name": names.get(int(r.club_id)),
                "non_bot_message_count": int(r.non_bot_message_count or 0),
                "first_message_at": r.first_message_at,
                "last_message_at": r.last_message_at,
            }
            for r in rows
        ]
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        notes=[f"activity_date={day.isoformat()} (America/New_York day)"],
    )


@translate_errors
def list_tickets(
    activity_date: str | None = None,
    club: str | None = None,
    category: str | None = None,
    chat_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Classified support tickets segmented from one day's group-chat transcripts,
    with category, brief/full summary, first-response time (frt_seconds) and
    duration. `category` is one of auto_deposit|manual_deposit|unfinished_deposit|
    cashout|unfinished_cashout|early_rakeback|rakeback|bonus|other. Use
    get_ticket_messages for the underlying messages. activity_date is a YYYY-MM-DD America/New_York day (default: yesterday ET). Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    day = parse_et_day(activity_date, default=_yesterday_et())
    cat = _validate_category(category)
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_row = resolve_club(session, club)
        query = session.query(GroupChatTicket).filter(
            GroupChatTicket.activity_date == day
        )
        if club_row is not None:
            query = query.filter(GroupChatTicket.club_id == club_row.id)
        if cat is not None:
            query = query.filter(GroupChatTicket.category == cat)
        if chat_id is not None:
            query = query.filter(GroupChatTicket.chat_id == int(chat_id))
        total = query.order_by(None).count()
        rows = (
            query.order_by(
                GroupChatTicket.club_id,
                GroupChatTicket.chat_id,
                GroupChatTicket.ticket_index,
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        enriched = {int(t.id): t for t in _enrich_tickets(session, rows)}
        items = []
        for row in rows:
            t = enriched.get(int(row.id))
            if t is None:
                continue
            message_ids = row.message_ids if isinstance(row.message_ids, list) else []
            items.append(
                {
                    "id": t.id,
                    "activity_date": t.activity_date,
                    "chat_id": t.chat_id,
                    "group_title": t.group_name,
                    "club_id": t.club_id,
                    "club_name": t.club_name,
                    "ticket_index": t.ticket_index,
                    "category": t.category,
                    "brief_summary": t.brief_summary,
                    "summary": t.summary,
                    "customer_first_message": t.customer_first_message,
                    "frt_seconds": t.frt_seconds,
                    "duration_seconds": t.duration_seconds,
                    "duration_source": t.duration_source,
                    "message_count": len(message_ids),
                    "start_msg_id": t.start_msg_id,
                    "end_msg_id": t.end_msg_id,
                    "events": t.events,
                }
            )
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=[
            f"activity_date={day.isoformat()} (America/New_York day)",
            "summaries are model-generated and may quote player names",
        ],
    )


@translate_errors
def get_ticket_messages(ticket_id: int, include_pii: bool = False) -> dict[str, Any]:
    """The chat messages that make up one ticket (from list_tickets), chronological,
    each tagged role=customer|admin|bot. Sender display names and usernames are
    masked unless include_pii. Message text is returned as stored."""
    with read_session() as session:
        ticket = session.get(GroupChatTicket, int(ticket_id))
        if ticket is None:
            raise not_found(f"ticket {ticket_id}")
        transcript = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == ticket.activity_date,
                GroupChatDailyTranscript.chat_id == int(ticket.chat_id),
            )
            .one_or_none()
        )
        if transcript is None:
            raise not_found(
                f"transcript for chat {ticket.chat_id} on {ticket.activity_date}"
            )
        group_title = _group_titles(session, {int(ticket.chat_id)}).get(
            int(ticket.chat_id)
        )
        names = club_name_map(session)
        message_ids = ticket.message_ids if isinstance(ticket.message_ids, list) else []
        sliced = slice_ticket_messages(
            messages=transcript.messages
            if isinstance(transcript.messages, list)
            else [],
            message_ids=message_ids,
            club_id=int(ticket.club_id),
            group_name=group_title,
        )
        item = {
            "ticket": {
                "id": int(ticket.id),
                "activity_date": ticket.activity_date,
                "chat_id": int(ticket.chat_id),
                "group_title": group_title,
                "club_id": int(ticket.club_id),
                "club_name": names.get(int(ticket.club_id)),
                "ticket_index": int(ticket.ticket_index),
                "category": ticket.category,
                "brief_summary": ticket.brief_summary,
                "summary": ticket.summary,
            },
            "messages": [_transcript_message(m) for m in sliced],
        }
    return single(
        item,
        include_pii=include_pii,
        notes=["activity_date is an America/New_York day; message dates are UTC"],
    )


@translate_errors
def get_chat_transcript(
    chat_id: int,
    activity_date: str | None = None,
    max_messages: int = 300,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Full stored transcript of one support group for one day (nightly MTProto
    extract): chronological messages with id, date (UTC), sender_id, display_name,
    username, is_bot, text, reply_to_msg_id, media_type. Returns at most
    `max_messages` (cap 1000) and says so in notes when truncated. activity_date is a YYYY-MM-DD America/New_York day (default: yesterday ET)."""
    day = parse_et_day(activity_date, default=_yesterday_et())
    cap = max(1, min(int(max_messages or 1), _MAX_TRANSCRIPT_MESSAGES))
    with read_session() as session:
        row = (
            session.query(GroupChatDailyTranscript)
            .filter(
                GroupChatDailyTranscript.activity_date == day,
                GroupChatDailyTranscript.chat_id == int(chat_id),
            )
            .one_or_none()
        )
        if row is None:
            raise not_found(f"transcript for chat {chat_id} on {day.isoformat()}")
        names = club_name_map(session)
        group_title = _group_titles(session, {int(chat_id)}).get(int(chat_id))
        messages = row.messages if isinstance(row.messages, list) else []
        stored = len(messages)
        shown = [_transcript_message(m) for m in messages[:cap]]
        item = {
            "id": int(row.id),
            "activity_date": row.activity_date,
            "chat_id": int(row.chat_id),
            "group_title": group_title,
            "club_id": int(row.club_id),
            "club_name": names.get(int(row.club_id)),
            "status": row.status,
            "error": row.error,
            "message_count": int(row.message_count or 0),
            "fetched_at": row.fetched_at,
            "analysis_status": row.analysis_status,
            "analyzed_at": row.analyzed_at,
            "messages_returned": len(shown),
            "messages_stored": stored,
            "messages": shown,
        }
    notes = [
        f"activity_date={day.isoformat()} (America/New_York day); message dates are UTC"
    ]
    if stored > len(shown):
        notes.append(f"truncated: showing first {len(shown)} of {stored} messages")
    return single(item, include_pii=include_pii, notes=notes)


@translate_errors
def get_kpis(
    granularity: str = "week",
    date_: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    club: str | None = None,
) -> dict[str, Any]:
    """Club KPIs for a day, week (Mon-Sun ET), month or custom range, each beside the
    previous period, like the dashboard KPIs tab: deposits (bound payments on every
    rail, counted once under the group's club), new players (support groups joined,
    how many have deposited since, first deposits), human first-response time
    (median, p90, % over 5 min), tickets by category, /deposit sessions that reached
    chips confirmed, and automation success (auto-deposit, early rakeback,
    auto-cashout, transfer). Also returns a trend (14 days / 8 weeks / 6 months) and a
    per-club split when no club is given. `date_` is any America/New_York day inside
    the period (default: yesterday, last complete week or last complete month);
    custom uses `from_`/`to` (YYYY-MM-DD, inclusive). Rates are fractions. Club: Club
    id (int), key (round_table|creator_club|clubgto) or slug
    (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    with read_session() as session:
        club_row = resolve_club(session, club)
        try:
            result = compute_kpis(
                session,
                granularity=granularity,
                anchor=parse_et_day(date_),
                start=parse_et_day(from_),
                end=parse_et_day(to),
                club_id=int(club_row.id) if club_row is not None else None,
            )
        except ValueError as exc:
            raise bad_input(str(exc)) from exc
    notes = [
        "dates are America/New_York days; weeks run Monday-Sunday",
        "rates are fractions (0.45 = 45%); response times are seconds",
        "test, staff and internal chats are excluded",
        "tickets are written nightly; period.tickets_pending marks days not in yet",
    ]
    if result["previous_truncated"]:
        notes.append(
            "period is still running; previous is cut to the same elapsed days"
        )
    return single(result, notes=notes)


def register(mcp: MCPServer) -> None:
    mcp.tool(name="deposit_funnel_summary", annotations=READ_ONLY)(
        deposit_funnel_summary
    )
    mcp.tool(name="get_kpis", annotations=READ_ONLY)(get_kpis)
    mcp.tool(name="list_group_chat_activity", annotations=READ_ONLY)(
        list_group_chat_activity
    )
    mcp.tool(name="list_tickets", annotations=READ_ONLY)(list_tickets)
    mcp.tool(name="get_ticket_messages", annotations=READ_ONLY)(get_ticket_messages)
    mcp.tool(name="get_chat_transcript", annotations=READ_ONLY)(get_chat_transcript)
