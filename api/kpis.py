"""Club KPIs: deposits, new players, response time, tickets, automation.

Everything is computed live from tables the bot and dashboard already write; there
is no snapshot table. SQL filters on UTC bounds and every row is bucketed into an
America/New_York calendar day in Python, so the same code runs on Postgres and
SQLite and weeks that cross a DST change keep the right edges. A week is Mon-Sun ET.

Counting rules (also documented in docs/KPIS.md):

- Deposits: bound payments only (``telegram_chat_id`` set, ``is_test`` false) from
  every rail, each row counted once under its group's club (falling back to the
  row's own ``club_id``). Stripe counts ``status='complete'`` sessions and Pool Pay
  counts trade-record-checked union requests.
- New players: support groups by ``groups.added_at``. A group converted once it has
  any deposit (same definition) up to now. First deposits counts groups whose
  earliest-ever deposit falls inside the period.
- Response time: ``group_chat_tickets`` by ``activity_date``; first response is
  ``compute_ticket_frt``; the median and the share answered within 2 minutes use
  human-answered tickets only.
- Automation: ``clubgg_rpa_events`` for the four bot automations. Rate is
  completed / (completed + failed + stuck), where stuck is a row still ``started``
  after ``STUCK_AFTER``; ``stale`` rows are reported but left out of the rate.

Test, staff and internal chats are excluded from every KPI.
"""

from __future__ import annotations

import calendar
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Literal, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.group_chat_ticket_helpers import compute_ticket_frt
from api.payments_helpers import is_analytics_excluded_group_title
from bot.services.response_audit import is_test_title
from db.models import (
    CashAppPayment,
    Club,
    ClubggRpaEvent,
    ClubPaymentMethod,
    CryptoPayment,
    Group,
    GroupChatTicket,
    ManualDepositRequest,
    PayPalPayment,
    StripeCheckoutSession,
    SupportGroupChat,
    VenmoPayment,
    ZellePayment,
)

ET = ZoneInfo("America/New_York")

Granularity = Literal["day", "week", "month", "custom"]
GRANULARITIES: tuple[str, ...] = ("day", "week", "month", "custom")

MAX_CUSTOM_DAYS = 366
STUCK_AFTER = timedelta(minutes=15)
FAST_FRT_SECONDS = 2 * 60
# Crypto rows are filtered on created_at in SQL and on paid_at in Python; widen the
# SQL window so a payment ingested a little after it was paid is not missed.
_CRYPTO_SLACK = timedelta(days=3)
_IN_CHUNK = 2000

TREND_LENGTH: dict[str, int] = {"day": 14, "week": 8, "month": 6}

RAIL_LABELS: dict[str, str] = {
    "venmo": "Venmo",
    "zelle": "Zelle",
    "cashapp": "Cash App",
    "paypal": "PayPal",
    "crypto": "Crypto",
    "stripe": "Stripe",
    "pool_pay": "Pool Pay",
}
_INGEST_RAILS: tuple[tuple[str, Any], ...] = (
    ("venmo", VenmoPayment),
    ("zelle", ZellePayment),
    ("cashapp", CashAppPayment),
    ("paypal", PayPalPayment),
)

CATEGORY_LABELS: dict[str, str] = {
    "auto_deposit": "Auto deposit",
    "manual_deposit": "Manual deposit",
    "unfinished_deposit": "Unfinished deposit",
    "cashout": "Cashout",
    "unfinished_cashout": "Unfinished cashout",
    "early_rakeback": "Early rakeback",
    "rakeback": "Rakeback",
    "bonus": "Bonus",
    "other": "Other",
}

AUTOMATION_LABELS: dict[str, str] = {
    "payment_auto_deposit": "Auto-deposit",
    "early_rakeback": "Early rakeback",
    "auto_cashout": "Auto-cashout",
    "transfer": "Transfer",
}

_CLUB_ORDER = ("Round Table", "Creator Club", "ClubGTO")


# --------------------------------------------------------------------------- dates


def utc_aware(value: datetime | None) -> datetime | None:
    """Naive datetimes in this schema are UTC (SQLite also strips tzinfo)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def et_day(value: datetime) -> date:
    return utc_aware(value).astimezone(ET).date()


def et_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ET).astimezone(timezone.utc)


def today_et(now: datetime | None = None) -> date:
    return (utc_aware(now) or datetime.now(timezone.utc)).astimezone(ET).date()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, months: int) -> date:
    idx = day.year * 12 + (day.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


@dataclass(frozen=True)
class Period:
    """Inclusive range of America/New_York days."""

    start: date
    end: date
    kind: str = "custom"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_utc(self) -> datetime:
        return et_day_start_utc(self.start)

    @property
    def end_utc(self) -> datetime:
        """Exclusive end: midnight ET after ``end``."""
        return et_day_start_utc(self.end + timedelta(days=1))

    def day_list(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]

    @property
    def label(self) -> str:
        return period_label(self)


def _fmt_md(day: date) -> str:
    return f"{day.strftime('%b')} {day.day}"


def period_label(p: Period) -> str:
    if p.kind == "day" or p.start == p.end:
        return f"{p.start.strftime('%a')}, {_fmt_md(p.start)}"
    if p.kind == "month" and p.start.day == 1:
        return p.start.strftime("%B %Y")
    if p.start.month == p.end.month and p.start.year == p.end.year:
        return f"{_fmt_md(p.start)} – {p.end.day}"
    if p.start.year == p.end.year:
        return f"{_fmt_md(p.start)} – {_fmt_md(p.end)}"
    return f"{_fmt_md(p.start)}, {p.start.year} – {_fmt_md(p.end)}, {p.end.year}"


@dataclass(frozen=True)
class PeriodPlan:
    granularity: str
    current: Period
    previous: Period
    trend: list[Period]
    # True when ``previous`` was cut to the same number of elapsed days as a
    # still-running ``current`` (week/month to date).
    previous_truncated: bool = False

    @property
    def window(self) -> Period:
        periods = [self.current, self.previous, *self.trend]
        return Period(min(p.start for p in periods), max(p.end for p in periods))


def resolve_period(
    granularity: str,
    *,
    anchor: date | None = None,
    start: date | None = None,
    end: date | None = None,
    today: date,
) -> PeriodPlan:
    """Current period, the one before it and the trend buckets for a request.

    Defaults: yesterday (day), last complete week (week), last complete month
    (month). Raises ``ValueError`` on bad input.
    """
    g = (granularity or "week").strip().lower()
    if g not in GRANULARITIES:
        raise ValueError(f"granularity must be one of: {', '.join(GRANULARITIES)}")

    if g == "custom":
        if start is None or end is None:
            raise ValueError("custom range needs both from and to")
        if start > end:
            raise ValueError("from must be on or before to")
        current = Period(start, end, "custom")
        if current.days > MAX_CUSTOM_DAYS:
            raise ValueError(f"custom range is limited to {MAX_CUSTOM_DAYS} days")
        prev_end = start - timedelta(days=1)
        previous = Period(prev_end - timedelta(days=current.days - 1), prev_end)
        return PeriodPlan(g, current, previous, _custom_buckets(current))

    if g == "day":
        day = anchor or today - timedelta(days=1)
        current = Period(day, day, "day")
        previous = Period(day - timedelta(days=1), day - timedelta(days=1), "day")
        n = TREND_LENGTH["day"]
        trend = [
            Period(d, d, "day")
            for d in (day - timedelta(days=i) for i in range(n - 1, -1, -1))
        ]
        return PeriodPlan(g, current, previous, trend)

    if g == "week":
        monday = monday_of(anchor) if anchor else monday_of(today) - timedelta(days=7)
        current = Period(monday, monday + timedelta(days=6), "week")
        prev_monday = monday - timedelta(days=7)
        previous = Period(prev_monday, prev_monday + timedelta(days=6), "week")
        n = TREND_LENGTH["week"]
        trend = []
        for i in range(n - 1, -1, -1):
            m = monday - timedelta(days=7 * i)
            trend.append(Period(m, m + timedelta(days=6), "week"))
        return _truncate_running(PeriodPlan(g, current, previous, trend), today)

    first = _month_start(anchor) if anchor else _add_months(_month_start(today), -1)
    current = Period(first, _month_end(first), "month")
    prev_first = _add_months(first, -1)
    previous = Period(prev_first, _month_end(prev_first), "month")
    n = TREND_LENGTH["month"]
    trend = []
    for i in range(n - 1, -1, -1):
        m = _add_months(first, -i)
        trend.append(Period(m, _month_end(m), "month"))
    return _truncate_running(PeriodPlan(g, current, previous, trend), today)


def _truncate_running(plan: PeriodPlan, today: date) -> PeriodPlan:
    """Week/month to date: compare against the same elapsed days of the prior one."""
    cur = plan.current
    if not (cur.start <= today <= cur.end):
        return plan
    elapsed = (today - cur.start).days
    prev = plan.previous
    prev_end = min(prev.end, prev.start + timedelta(days=elapsed))
    return PeriodPlan(
        plan.granularity,
        cur,
        Period(prev.start, prev_end, prev.kind),
        plan.trend,
        previous_truncated=prev_end < prev.end,
    )


def _custom_buckets(current: Period) -> list[Period]:
    if current.days <= 31:
        return [Period(d, d, "day") for d in current.day_list()]
    out: list[Period] = []
    if current.days <= 26 * 7:
        cursor = current.start
        while cursor <= current.end:
            stop = min(monday_of(cursor) + timedelta(days=6), current.end)
            out.append(Period(cursor, stop, "week"))
            cursor = stop + timedelta(days=1)
        return out
    cursor = current.start
    while cursor <= current.end:
        stop = min(_month_end(cursor), current.end)
        out.append(Period(cursor, stop, "month"))
        cursor = stop + timedelta(days=1)
    return out


# ------------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class GroupInfo:
    chat_id: int
    club_id: int
    title: str | None
    added_day: date | None


@dataclass(frozen=True, slots=True)
class DepositRow:
    day: date
    chat_id: int
    club_id: int | None
    rail: str
    amount_cents: int


@dataclass(frozen=True, slots=True)
class TicketRow:
    day: date
    club_id: int | None
    category: str
    frt_seconds: int | None


@dataclass(frozen=True, slots=True)
class RpaRow:
    day: date
    club_id: int | None
    source: str
    outcome: str  # completed | failed | stuck | stale | in_progress


@dataclass(frozen=True, slots=True)
class NewGroupRow:
    day: date
    chat_id: int
    club_id: int


@dataclass(frozen=True, slots=True)
class FirstDepositRow:
    day: date
    chat_id: int
    club_id: int | None


def _staff_handles() -> tuple[str, ...]:
    from club_gc_settings import GC_USERS_TO_INVITE

    handles = {
        h.strip().lstrip("@").casefold()
        for users in GC_USERS_TO_INVITE.values()
        for h in users
        if h and h.strip().lstrip("@")
    }
    return tuple(sorted(handles))


def is_kpi_excluded_title(title: str | None, staff_handles: Sequence[str] = ()) -> bool:
    """Test, staging or staff-only support group titles left out of every KPI."""
    if not title or not title.strip():
        return False
    if is_analytics_excluded_group_title(title) or is_test_title(title):
        return True
    folded = title.casefold()
    return any(handle in folded for handle in staff_handles)


def load_groups(db: Session) -> dict[int, GroupInfo]:
    out: dict[int, GroupInfo] = {}
    for chat_id, club_id, name, added_at in db.query(
        Group.chat_id, Group.club_id, Group.name, Group.added_at
    ):
        out[int(chat_id)] = GroupInfo(
            chat_id=int(chat_id),
            club_id=int(club_id),
            title=str(name) if name is not None else None,
            added_day=et_day(added_at) if added_at is not None else None,
        )
    return out


def kpi_excluded_chat_ids(db: Session, groups: dict[int, GroupInfo]) -> set[int]:
    """Chats excluded from KPIs: test/staging titles, staff groups, internal chats."""
    handles = _staff_handles()
    excluded = {
        chat_id
        for chat_id, info in groups.items()
        if is_kpi_excluded_title(info.title, handles)
    }
    for chat_id, title, is_internal in db.query(
        SupportGroupChat.telegram_chat_id,
        SupportGroupChat.telegram_chat_title,
        SupportGroupChat.is_internal,
    ):
        if is_internal or is_kpi_excluded_title(title, handles):
            excluded.add(int(chat_id))
    return excluded


def _club_for(chat_id: int | None, row_club_id: int | None, groups) -> int | None:
    if chat_id is not None:
        info = groups.get(int(chat_id))
        if info is not None:
            return info.club_id
    return int(row_club_id) if row_club_id is not None else None


def _dollars_to_cents(amount: Any) -> int:
    return int((Decimal(str(amount or 0)) * 100).to_integral_value())


def _crypto_occurred_at(paid_at: str | None, created_at: datetime) -> datetime:
    from bot.services.payment_chip_match import parse_payment_reference_at

    parsed = parse_payment_reference_at(paid_at=paid_at, created_at=created_at)
    return utc_aware(parsed or created_at)


def fetch_deposits(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    *,
    groups: dict[int, GroupInfo],
    excluded: set[int],
) -> list[DepositRow]:
    """Bound deposit rows from every rail with occurred time in [start, end)."""
    out: list[DepositRow] = []

    def keep(chat_id, row_club_id, occurred: datetime, rail: str, cents: int):
        if chat_id is None or int(chat_id) in excluded:
            return
        occurred = utc_aware(occurred)
        if not (start_utc <= occurred < end_utc):
            return
        out.append(
            DepositRow(
                day=et_day(occurred),
                chat_id=int(chat_id),
                club_id=_club_for(chat_id, row_club_id, groups),
                rail=rail,
                amount_cents=int(cents or 0),
            )
        )

    for rail, cls in _INGEST_RAILS:
        rows = db.query(
            cls.telegram_chat_id, cls.club_id, cls.created_at, cls.amount_cents
        ).filter(
            cls.telegram_chat_id.isnot(None),
            cls.is_test.is_(False),
            cls.created_at >= start_utc,
            cls.created_at < end_utc,
        )
        for chat_id, club_id, created_at, cents in rows:
            keep(chat_id, club_id, created_at, rail, cents)

    crypto = db.query(
        CryptoPayment.telegram_chat_id,
        CryptoPayment.club_id,
        CryptoPayment.created_at,
        CryptoPayment.paid_at,
        CryptoPayment.amount_cents,
    ).filter(
        CryptoPayment.telegram_chat_id.isnot(None),
        CryptoPayment.is_test.is_(False),
        CryptoPayment.created_at >= start_utc - _CRYPTO_SLACK,
        CryptoPayment.created_at < end_utc + _CRYPTO_SLACK,
    )
    for chat_id, club_id, created_at, paid_at, cents in crypto:
        keep(
            chat_id, club_id, _crypto_occurred_at(paid_at, created_at), "crypto", cents
        )

    stripe_at = func.coalesce(
        StripeCheckoutSession.completed_at, StripeCheckoutSession.created_at
    )
    stripe = db.query(
        StripeCheckoutSession.telegram_chat_id,
        StripeCheckoutSession.club_id,
        StripeCheckoutSession.completed_at,
        StripeCheckoutSession.created_at,
        StripeCheckoutSession.amount_cents,
    ).filter(
        StripeCheckoutSession.status == "complete",
        stripe_at >= start_utc,
        stripe_at < end_utc,
    )
    for chat_id, club_id, completed_at, created_at, cents in stripe:
        keep(chat_id, club_id, completed_at or created_at, "stripe", cents)

    pool = (
        db.query(
            ManualDepositRequest.telegram_chat_id,
            ManualDepositRequest.club_id,
            ManualDepositRequest.created_at,
            ManualDepositRequest.amount,
        )
        .join(ClubPaymentMethod, ClubPaymentMethod.id == ManualDepositRequest.method_id)
        .filter(
            ClubPaymentMethod.pool_pay_type == "union_method",
            ManualDepositRequest.trade_record_checked.is_(True),
            ManualDepositRequest.created_at >= start_utc,
            ManualDepositRequest.created_at < end_utc,
        )
    )
    for chat_id, club_id, created_at, amount in pool:
        keep(chat_id, club_id, created_at, "pool_pay", _dollars_to_cents(amount))

    return out


def _chunks(values: Sequence[int], size: int = _IN_CHUNK) -> Iterable[list[int]]:
    for i in range(0, len(values), size):
        yield list(values[i : i + size])


def fetch_first_deposit_at(db: Session, chat_ids: Iterable[int]) -> dict[int, datetime]:
    """Earliest-ever deposit time per chat (same rails and rules as deposits)."""
    ids = sorted({int(c) for c in chat_ids})
    first: dict[int, datetime] = {}

    def seen(chat_id, when):
        if chat_id is None or when is None:
            return
        when = utc_aware(when)
        cid = int(chat_id)
        if cid not in first or when < first[cid]:
            first[cid] = when

    for chunk in _chunks(ids):
        for _rail, cls in _INGEST_RAILS:
            rows = (
                db.query(cls.telegram_chat_id, func.min(cls.created_at))
                .filter(cls.telegram_chat_id.in_(chunk), cls.is_test.is_(False))
                .group_by(cls.telegram_chat_id)
            )
            for chat_id, when in rows:
                seen(chat_id, when)
        crypto = db.query(
            CryptoPayment.telegram_chat_id,
            CryptoPayment.created_at,
            CryptoPayment.paid_at,
        ).filter(
            CryptoPayment.telegram_chat_id.in_(chunk),
            CryptoPayment.is_test.is_(False),
        )
        for chat_id, created_at, paid_at in crypto:
            seen(chat_id, _crypto_occurred_at(paid_at, created_at))
        stripe_at = func.coalesce(
            StripeCheckoutSession.completed_at, StripeCheckoutSession.created_at
        )
        stripe = (
            db.query(StripeCheckoutSession.telegram_chat_id, func.min(stripe_at))
            .filter(
                StripeCheckoutSession.telegram_chat_id.in_(chunk),
                StripeCheckoutSession.status == "complete",
            )
            .group_by(StripeCheckoutSession.telegram_chat_id)
        )
        for chat_id, when in stripe:
            seen(chat_id, when)
        pool = (
            db.query(
                ManualDepositRequest.telegram_chat_id,
                func.min(ManualDepositRequest.created_at),
            )
            .join(
                ClubPaymentMethod,
                ClubPaymentMethod.id == ManualDepositRequest.method_id,
            )
            .filter(
                ManualDepositRequest.telegram_chat_id.in_(chunk),
                ClubPaymentMethod.pool_pay_type == "union_method",
                ManualDepositRequest.trade_record_checked.is_(True),
            )
            .group_by(ManualDepositRequest.telegram_chat_id)
        )
        for chat_id, when in pool:
            seen(chat_id, when)
    return first


def fetch_tickets(
    db: Session, window: Period, *, excluded: set[int]
) -> list[TicketRow]:
    rows = db.query(
        GroupChatTicket.activity_date,
        GroupChatTicket.chat_id,
        GroupChatTicket.club_id,
        GroupChatTicket.category,
        GroupChatTicket.events,
    ).filter(
        GroupChatTicket.activity_date >= window.start,
        GroupChatTicket.activity_date <= window.end,
    )
    out: list[TicketRow] = []
    for day, chat_id, club_id, category, events in rows:
        if int(chat_id) in excluded:
            continue
        out.append(
            TicketRow(
                day=day,
                club_id=int(club_id) if club_id is not None else None,
                category=str(category or "other"),
                frt_seconds=compute_ticket_frt(events),
            )
        )
    return out


def rpa_outcome(status: str | None, started_at: datetime, now: datetime) -> str:
    s = (status or "").strip().lower()
    if s in ("completed", "failed", "stale"):
        return s
    if s == "started" and now - utc_aware(started_at) >= STUCK_AFTER:
        return "stuck"
    return "in_progress"


def fetch_rpa(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    *,
    now: datetime,
    groups: dict[int, GroupInfo],
    excluded: set[int],
) -> list[RpaRow]:
    rows = db.query(
        ClubggRpaEvent.started_at,
        ClubggRpaEvent.source,
        ClubggRpaEvent.status,
        ClubggRpaEvent.club_id,
        ClubggRpaEvent.telegram_chat_id,
    ).filter(
        ClubggRpaEvent.source.in_(tuple(AUTOMATION_LABELS)),
        ClubggRpaEvent.started_at >= start_utc,
        ClubggRpaEvent.started_at < end_utc,
    )
    out: list[RpaRow] = []
    for started_at, source, status, club_id, chat_id in rows:
        if chat_id is not None and int(chat_id) in excluded:
            continue
        out.append(
            RpaRow(
                day=et_day(started_at),
                club_id=_club_for(chat_id, club_id, groups),
                source=str(source),
                outcome=rpa_outcome(status, started_at, now),
            )
        )
    return out


# --------------------------------------------------------------------- aggregation


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _usd(cents: int) -> float:
    return round(cents / 100, 2)


def summarize_deposits(rows: Sequence[DepositRow]) -> dict[str, Any]:
    total_cents = sum(r.amount_cents for r in rows)
    by_rail_count: Counter[str] = Counter()
    by_rail_cents: Counter[str] = Counter()
    for r in rows:
        by_rail_count[r.rail] += 1
        by_rail_cents[r.rail] += r.amount_cents
    by_rail = [
        {
            "rail": rail,
            "label": RAIL_LABELS.get(rail, rail),
            "count": by_rail_count[rail],
            "amount_usd": _usd(by_rail_cents[rail]),
        }
        for rail in by_rail_count
    ]
    by_rail.sort(key=lambda x: (-x["amount_usd"], x["rail"]))
    return {
        "count": len(rows),
        "amount_usd": _usd(total_cents),
        "avg_usd": _usd(total_cents // len(rows)) if rows else None,
        "by_rail": by_rail,
    }


def summarize_new_players(
    joined: Sequence[NewGroupRow],
    first_deposits: Sequence[FirstDepositRow],
    first_deposit_at: dict[int, datetime],
) -> dict[str, Any]:
    converted = sum(1 for g in joined if g.chat_id in first_deposit_at)
    return {
        "joined": len(joined),
        "converted": converted,
        "conversion_rate": _rate(converted, len(joined)),
        "first_deposits": len(first_deposits),
    }


def summarize_response(tickets: Sequence[TicketRow]) -> dict[str, Any]:
    frts = sorted(t.frt_seconds for t in tickets if t.frt_seconds is not None)
    fast = sum(1 for v in frts if v <= FAST_FRT_SECONDS)
    return {
        "tickets": len(tickets),
        "human_answered": len(frts),
        "no_human_reply": len(tickets) - len(frts),
        "median_seconds": float(statistics.median(frts)) if frts else None,
        "under_2m": fast,
        "pct_under_2m": _rate(fast, len(frts)),
    }


def summarize_tickets(tickets: Sequence[TicketRow]) -> dict[str, Any]:
    counts = Counter(t.category for t in tickets)
    keys = list(CATEGORY_LABELS) + sorted(k for k in counts if k not in CATEGORY_LABELS)
    by_category = [
        {"category": k, "label": CATEGORY_LABELS.get(k, k), "count": counts.get(k, 0)}
        for k in keys
    ]
    by_category.sort(key=lambda x: -x["count"])
    return {"total": len(tickets), "by_category": by_category}


def _automation_row(source: str, label: str, rows: Sequence[RpaRow]) -> dict[str, Any]:
    c = Counter(r.outcome for r in rows)
    attempted = c["completed"] + c["failed"] + c["stuck"]
    return {
        "source": source,
        "label": label,
        "attempted": attempted,
        "completed": c["completed"],
        "failed": c["failed"],
        "stuck": c["stuck"],
        "stale": c["stale"],
        "in_progress": c["in_progress"],
        "rate": _rate(c["completed"], attempted),
    }


def summarize_automation(rows: Sequence[RpaRow]) -> dict[str, Any]:
    by_source: dict[str, list[RpaRow]] = defaultdict(list)
    for r in rows:
        by_source[r.source].append(r)
    return {
        "overall": _automation_row("all", "All automations", rows),
        "by_source": [
            _automation_row(src, label, by_source.get(src, []))
            for src, label in AUTOMATION_LABELS.items()
        ],
    }


# ---------------------------------------------------------------------- assembling


class _DayIndex:
    """Rows grouped by ET day, sliced by period and club."""

    def __init__(self, rows: Iterable[Any]):
        self._by_day: dict[date, list[Any]] = defaultdict(list)
        for row in rows:
            self._by_day[row.day].append(row)

    def slice(self, period: Period, club_id: int | None) -> list[Any]:
        out: list[Any] = []
        for day in period.day_list():
            for row in self._by_day.get(day, ()):
                if club_id is None or row.club_id == club_id:
                    out.append(row)
        return out


@dataclass
class _Data:
    deposits: _DayIndex
    joined: _DayIndex
    first_deposits: _DayIndex
    first_deposit_at: dict[int, datetime]
    tickets: _DayIndex
    rpa: _DayIndex


def _snapshot(data: _Data, period: Period, club_id: int | None) -> dict[str, Any]:
    tickets = data.tickets.slice(period, club_id)
    return {
        "deposits": summarize_deposits(data.deposits.slice(period, club_id)),
        "new_players": summarize_new_players(
            data.joined.slice(period, club_id),
            data.first_deposits.slice(period, club_id),
            data.first_deposit_at,
        ),
        "response_time": summarize_response(tickets),
        "tickets": summarize_tickets(tickets),
        "automation": summarize_automation(data.rpa.slice(period, club_id)),
    }


def headline(snapshot: dict[str, Any]) -> dict[str, Any]:
    dep = snapshot["deposits"]
    new = snapshot["new_players"]
    rt = snapshot["response_time"]
    auto = snapshot["automation"]["overall"]
    return {
        "deposits_count": dep["count"],
        "deposits_usd": dep["amount_usd"],
        "joined": new["joined"],
        "converted": new["converted"],
        "conversion_rate": new["conversion_rate"],
        "first_deposits": new["first_deposits"],
        "tickets": snapshot["tickets"]["total"],
        "median_frt_seconds": rt["median_seconds"],
        "pct_under_2m": rt["pct_under_2m"],
        "automation_attempted": auto["attempted"],
        "automation_rate": auto["rate"],
    }


def _period_out(
    p: Period, *, today: date, tickets_through: date | None, kind: str
) -> dict[str, Any]:
    return {
        "start": p.start,
        "end": p.end,
        "label": period_label(Period(p.start, p.end, kind)),
        "days": p.days,
        "is_partial": p.end >= today,
        "tickets_pending": tickets_through is None or p.end > tickets_through,
    }


def _ordered_clubs(db: Session) -> list[tuple[int, str]]:
    rows = [(int(cid), str(name)) for cid, name in db.query(Club.id, Club.name)]
    rows = [r for r in rows if r[1] in _CLUB_ORDER] or rows
    return sorted(rows, key=lambda r: (_CLUB_ORDER.index(r[1]), r[0]))


def compute_kpis(
    db: Session,
    *,
    granularity: str = "week",
    anchor: date | None = None,
    start: date | None = None,
    end: date | None = None,
    club_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """All six KPIs for one period, the period before it, a trend and a club split.

    Raises ``ValueError`` for an invalid period.
    """
    now = utc_aware(now) or datetime.now(timezone.utc)
    today = today_et(now)
    plan = resolve_period(granularity, anchor=anchor, start=start, end=end, today=today)
    window = plan.window
    w_start, w_end = window.start_utc, window.end_utc

    groups = load_groups(db)
    excluded = kpi_excluded_chat_ids(db, groups)

    deposits = fetch_deposits(db, w_start, w_end, groups=groups, excluded=excluded)
    joined = [
        NewGroupRow(day=g.added_day, chat_id=g.chat_id, club_id=g.club_id)
        for g in groups.values()
        if g.added_day is not None
        and window.start <= g.added_day <= window.end
        and g.chat_id not in excluded
    ]
    first_deposit_at = fetch_first_deposit_at(
        db, {r.chat_id for r in deposits} | {g.chat_id for g in joined}
    )
    first_deposits = []
    for chat_id, when in first_deposit_at.items():
        if chat_id in excluded:
            continue
        day = et_day(when)
        if window.start <= day <= window.end:
            first_deposits.append(
                FirstDepositRow(
                    day=day, chat_id=chat_id, club_id=_club_for(chat_id, None, groups)
                )
            )
    # Club for a first deposit whose chat has no groups row: take the deposit row's.
    deposit_club = {r.chat_id: r.club_id for r in deposits}
    first_deposits = [
        f
        if f.club_id is not None
        else FirstDepositRow(f.day, f.chat_id, deposit_club.get(f.chat_id))
        for f in first_deposits
    ]

    data = _Data(
        deposits=_DayIndex(deposits),
        joined=_DayIndex(joined),
        first_deposits=_DayIndex(first_deposits),
        first_deposit_at=first_deposit_at,
        tickets=_DayIndex(fetch_tickets(db, window, excluded=excluded)),
        rpa=_DayIndex(
            fetch_rpa(db, w_start, w_end, now=now, groups=groups, excluded=excluded)
        ),
    )
    tickets_through = db.query(func.max(GroupChatTicket.activity_date)).scalar()

    current = _snapshot(data, plan.current, club_id)
    previous = _snapshot(data, plan.previous, club_id)

    def pout(p: Period, kind: str | None = None) -> dict[str, Any]:
        return _period_out(
            p, today=today, tickets_through=tickets_through, kind=kind or p.kind
        )

    trend = [{**pout(p), **headline(_snapshot(data, p, club_id))} for p in plan.trend]

    clubs = _ordered_clubs(db)
    club_name = dict(clubs).get(club_id) if club_id is not None else None
    if club_id is not None and club_name is None:
        club_name = db.query(Club.name).filter(Club.id == club_id).scalar()
    by_club: list[dict[str, Any]] = []
    if club_id is None:
        by_club = [
            {
                "club_id": cid,
                "club_name": name,
                **headline(_snapshot(data, plan.current, cid)),
            }
            for cid, name in clubs
        ]

    return {
        "granularity": plan.granularity,
        "club_id": club_id,
        "club_name": club_name,
        "period": pout(plan.current),
        "previous": pout(
            plan.previous, "custom" if plan.previous_truncated else plan.previous.kind
        ),
        "previous_truncated": plan.previous_truncated,
        "current": current,
        "prior": previous,
        "trend": trend,
        "by_club": by_club,
        "freshness": {
            "generated_at": now,
            "today": today,
            "tickets_complete_through": tickets_through,
            "stuck_after_minutes": int(STUCK_AFTER.total_seconds() // 60),
        },
    }
