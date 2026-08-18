"""Time-range CSV exports for cashout records, bonuses, and group-chat tickets."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from api.group_chat_ticket_helpers import compute_ticket_duration, index_messages_by_id
from bot.services.staff_cashout_records import STATUSES, compute_ledger
from db.models import (
    BonusRecord,
    Club,
    Group,
    GroupChatDailyTranscript,
    GroupChatTicket,
    StaffCashoutRecord,
)

_ET = ZoneInfo("America/New_York")
_EVENT_KEYS = (
    ("customer_first_message", "customer_first_message_utc"),
    ("admin_first_response", "admin_first_response_utc"),
    ("resolution", "resolution_utc"),
    ("escalation", "escalation_utc"),
)
_CLOCK_PRIORITY = (
    ("customer_first_message", "customer_first_message"),
    ("resolution", "resolution"),
    ("admin_first_response", "admin_first_response"),
)


def parse_inclusive_date_range(from_raw: str, to_raw: str) -> tuple[date, date]:
    """Parse YYYY-MM-DD bounds; raise ValueError on bad input or inverted range."""

    def _one(raw: str) -> date:
        text = (raw or "").strip()[:10]
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"Invalid date: {raw!r}") from exc

    start = _one(from_raw)
    end = _one(to_raw)
    if start > end:
        raise ValueError("from must be on or before to")
    return start, end


def et_range_to_utc_naive(from_day: date, to_day: date) -> tuple[datetime, datetime]:
    """Inclusive ET calendar days → naive UTC bounds for created_at columns."""

    start = (
        datetime.combine(from_day, time.min, tzinfo=_ET)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    end = (
        datetime.combine(to_day, time(23, 59, 59, 999999), tzinfo=_ET)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    return start, end


def escape_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, datetime):
        text = _fmt_dt(value)
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    if any(ch in text for ch in '",\n\r'):
        return '"' + text.replace('"', '""') + '"'
    return text


def rows_to_csv_bytes(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    lines = [",".join(escape_csv_cell(c) for c in header)]
    for row in rows:
        lines.append(",".join(escape_csv_cell(c) for c in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def csv_streaming_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _flatten_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _parse_event_ts(events: dict[str, Any] | None, key: str) -> datetime | None:
    if not isinstance(events, dict):
        return None
    raw = events.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def time_bucket_et(hour: int) -> str:
    if hour >= 23 or hour < 1:
        return "11pm-1am"
    if hour < 3:
        return "1am-3am"
    if hour < 9:
        return "3am-9am"
    if hour < 11:
        return "9am-11am"
    if hour < 13:
        return "11am-1pm"
    if hour < 15:
        return "1pm-3pm"
    if hour < 17:
        return "3pm-5pm"
    if hour < 19:
        return "5pm-7pm"
    if hour < 21:
        return "7pm-9pm"
    return "9pm-11pm"


def _ticket_clock_fields(events: dict[str, Any] | None) -> dict[str, Any]:
    picked_ts: datetime | None = None
    picked_source = ""
    for event_key, source_label in _CLOCK_PRIORITY:
        ts = _parse_event_ts(events, event_key)
        if ts is not None:
            picked_ts = ts
            picked_source = source_label
            break

    if picked_ts is None:
        return {
            "clock_ts_utc": "",
            "clock_ts_et": "",
            "hour_et": "",
            "dow_et": "",
            "time_bucket_et": "",
            "clock_source": "",
        }

    et = picked_ts.astimezone(_ET)
    return {
        "clock_ts_utc": picked_ts.isoformat(),
        "clock_ts_et": et.isoformat(),
        "hour_et": et.hour,
        "dow_et": et.strftime("%A"),
        "time_bucket_et": time_bucket_et(et.hour),
        "clock_source": picked_source,
    }


def _event_columns(events: dict[str, Any] | None) -> dict[str, str]:
    ev = events if isinstance(events, dict) else {}
    out: dict[str, str] = {}
    for event_key, col in _EVENT_KEYS:
        raw = ev.get(event_key)
        if raw is None:
            out[col] = ""
            continue
        text = str(raw).strip()
        out[col] = text
    return out


TICKET_CSV_HEADER = [
    "id",
    "activity_date",
    "club_name",
    "club_id",
    "chat_id",
    "group_name",
    "ticket_index",
    "category",
    "start_msg_id",
    "end_msg_id",
    "customer_first_message_utc",
    "admin_first_response_utc",
    "resolution_utc",
    "escalation_utc",
    "clock_ts_utc",
    "clock_ts_et",
    "hour_et",
    "dow_et",
    "time_bucket_et",
    "clock_source",
    "duration_seconds",
    "duration_source",
    "brief_summary",
    "summary",
    "prompt_version",
    "model",
    "created_at",
    "updated_at",
]

CASHOUT_CSV_HEADER = [
    "id",
    "created_at",
    "club_id",
    "club_name",
    "group_title",
    "gg_player_id",
    "amount",
    "status",
    "sent",
    "remaining",
    "trigger",
    "tracks_money_sent",
    "payment_methods",
    "payout_details",
    "send_count",
    "send_total",
    "cashier_job_id",
    "chat_id",
    "recorded_by_telegram_user_id",
    "updated_at",
]

BONUS_CSV_HEADER = [
    "id",
    "created_at",
    "club_id",
    "club_name",
    "player_username",
    "gg_player_id",
    "group_title",
    "amount",
    "bonus_type_name",
    "custom_description",
    "admin_telegram_user_id",
    "chat_id",
    "player_details_id",
]


def build_cashout_records_csv(
    session: Session,
    *,
    from_day: date,
    to_day: date,
    club_id: int | None = None,
    status: str | None = None,
) -> bytes:
    if status is not None and status not in STATUSES:
        raise ValueError("status must be active, cleared, or oversent")

    start, end = et_range_to_utc_naive(from_day, to_day)
    club_names = {
        int(row.id): str(row.name)
        for row in session.query(Club.id, Club.name).all()
    }

    query = (
        session.query(StaffCashoutRecord)
        .options(
            joinedload(StaffCashoutRecord.payments),
            joinedload(StaffCashoutRecord.money_sends),
        )
        .filter(
            StaffCashoutRecord.created_at >= start,
            StaffCashoutRecord.created_at <= end,
        )
        .order_by(StaffCashoutRecord.created_at.asc(), StaffCashoutRecord.id.asc())
    )
    if club_id is not None:
        query = query.filter(StaffCashoutRecord.club_id == int(club_id))

    rows: list[list[Any]] = []
    for record in query.all():
        payments = sorted(record.payments, key=lambda p: (p.sort_order or 0, p.id or 0))
        sends = sorted(record.money_sends, key=lambda s: s.created_at or datetime.min)
        send_dicts = [
            {"amount": s.amount}
            for s in sends
        ]
        ledger = compute_ledger(bool(record.tracks_money_sent), record.amount, send_dicts)
        row_status = str(ledger.get("status") or "cleared")
        if status is not None and row_status != status:
            continue

        methods = [
            (p.method_display_name or "").strip()
            for p in payments
            if (p.method_display_name or "").strip()
        ]
        details = [
            (p.payout_details or "").strip()
            for p in payments
            if (p.payout_details or "").strip()
        ]
        send_total = sum((Decimal(str(s.amount or 0)) for s in sends), Decimal("0"))

        rows.append(
            [
                record.id,
                record.created_at,
                record.club_id,
                club_names.get(int(record.club_id), ""),
                record.group_title,
                record.gg_player_id or "",
                record.amount,
                row_status,
                ledger.get("sent", Decimal("0")),
                ledger.get("remaining", Decimal("0")),
                record.trigger,
                bool(record.tracks_money_sent),
                "; ".join(methods),
                "; ".join(details),
                len(sends),
                send_total,
                record.cashier_job_id or "",
                record.chat_id or "",
                record.recorded_by_telegram_user_id or "",
                record.updated_at,
            ]
        )

    return rows_to_csv_bytes(CASHOUT_CSV_HEADER, rows)


def build_bonus_records_csv(
    session: Session,
    *,
    from_day: date,
    to_day: date,
    club_id: int | None = None,
    bonus_type_id: int | None = None,
    other: bool = False,
) -> bytes:
    start, end = et_range_to_utc_naive(from_day, to_day)
    club_names = {
        int(row.id): str(row.name)
        for row in session.query(Club.id, Club.name).all()
    }

    query = (
        session.query(BonusRecord)
        .options(joinedload(BonusRecord.bonus_type))
        .filter(
            BonusRecord.created_at >= start,
            BonusRecord.created_at <= end,
        )
        .order_by(BonusRecord.created_at.asc(), BonusRecord.id.asc())
    )
    if club_id is not None:
        query = query.filter(BonusRecord.club_id == int(club_id))
    if other:
        query = query.filter(BonusRecord.bonus_type_id.is_(None))
    elif bonus_type_id is not None:
        query = query.filter(BonusRecord.bonus_type_id == int(bonus_type_id))

    rows: list[list[Any]] = []
    for record in query.all():
        type_name = record.bonus_type.name if record.bonus_type else ""
        cid = int(record.club_id) if record.club_id is not None else None
        rows.append(
            [
                record.id,
                record.created_at,
                cid or "",
                club_names.get(cid, "") if cid is not None else "",
                record.player_username,
                record.gg_player_id or "",
                record.group_title or "",
                record.amount,
                type_name,
                record.custom_description or "",
                record.admin_telegram_user_id or "",
                record.chat_id or "",
                record.player_details_id or "",
            ]
        )

    return rows_to_csv_bytes(BONUS_CSV_HEADER, rows)


def build_group_chat_tickets_csv(
    session: Session,
    *,
    from_day: date,
    to_day: date,
    club_id: int | None = None,
    category: str | None = None,
) -> bytes:
    club_names = {
        int(row.id): str(row.name)
        for row in session.query(Club.id, Club.name).all()
    }

    query = (
        session.query(GroupChatTicket)
        .filter(
            GroupChatTicket.activity_date >= from_day,
            GroupChatTicket.activity_date <= to_day,
        )
        .order_by(
            GroupChatTicket.activity_date.asc(),
            GroupChatTicket.club_id.asc(),
            GroupChatTicket.chat_id.asc(),
            GroupChatTicket.ticket_index.asc(),
        )
    )
    if club_id is not None:
        query = query.filter(GroupChatTicket.club_id == int(club_id))
    if category is not None:
        query = query.filter(GroupChatTicket.category == category.strip().lower())

    tickets = query.all()
    if not tickets:
        return rows_to_csv_bytes(TICKET_CSV_HEADER, [])

    chat_ids = {int(t.chat_id) for t in tickets}
    groups = {
        int(g.chat_id): (str(g.name).strip() if g.name else "")
        for g in session.query(Group).filter(Group.chat_id.in_(chat_ids)).all()
    }

    transcript_keys = {(t.activity_date, int(t.chat_id)) for t in tickets}
    transcript_dates = {d for d, _ in transcript_keys}
    transcripts = (
        session.query(GroupChatDailyTranscript)
        .filter(
            GroupChatDailyTranscript.activity_date.in_(transcript_dates),
            GroupChatDailyTranscript.chat_id.in_(chat_ids),
        )
        .all()
    )
    messages_by_day_chat: dict[tuple[date, int], dict[int, dict[str, Any]]] = {
        (t.activity_date, int(t.chat_id)): index_messages_by_id(t.messages)
        for t in transcripts
    }

    rows: list[list[Any]] = []
    for ticket in tickets:
        events = ticket.events if isinstance(ticket.events, dict) else None
        msg_index = messages_by_day_chat.get(
            (ticket.activity_date, int(ticket.chat_id)), {}
        )
        duration_seconds, duration_source = compute_ticket_duration(
            events,
            ticket.message_ids if isinstance(ticket.message_ids, list) else None,
            msg_index or None,
        )
        event_cols = _event_columns(events)
        clock_cols = _ticket_clock_fields(events)

        rows.append(
            [
                ticket.id,
                ticket.activity_date.isoformat(),
                club_names.get(int(ticket.club_id), ""),
                ticket.club_id,
                ticket.chat_id,
                groups.get(int(ticket.chat_id), ""),
                ticket.ticket_index,
                ticket.category,
                ticket.start_msg_id,
                ticket.end_msg_id,
                event_cols["customer_first_message_utc"],
                event_cols["admin_first_response_utc"],
                event_cols["resolution_utc"],
                event_cols["escalation_utc"],
                clock_cols["clock_ts_utc"],
                clock_cols["clock_ts_et"],
                clock_cols["hour_et"],
                clock_cols["dow_et"],
                clock_cols["time_bucket_et"],
                clock_cols["clock_source"],
                duration_seconds if duration_seconds is not None else "",
                duration_source or "",
                _flatten_text(ticket.brief_summary),
                _flatten_text(ticket.summary),
                ticket.prompt_version,
                ticket.model,
                _fmt_dt(ticket.created_at),
                _fmt_dt(ticket.updated_at),
            ]
        )

    return rows_to_csv_bytes(TICKET_CSV_HEADER, rows)
