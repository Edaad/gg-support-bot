"""Deposit funnel analytics API — /deposit through chips credited."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.payments_helpers import apply_analytics_chat_exclusion, cents_to_usd
from api.schemas_payments import (
    DepositFunnelEventListResponse,
    DepositFunnelEventRead,
    DepositFunnelLatencyStep,
    DepositFunnelLatencySummaryResponse,
    DepositFunnelStepCount,
    DepositFunnelSummaryResponse,
    DepositFunnelUnionBreakdown,
)
from bot.services.deposit_funnel_events import (
    STEP_BIND_SETUP_COMPLETED,
    STEP_CHIPS_CONFIRMED,
    STEP_DEPOSIT_STARTED,
    STEP_PAYMENT_BOUND,
    STEP_UNION_CHOSEN,
    display_funnel_step_order,
)
from bot.services.round_table_unions import is_round_table_club
from db.connection import get_db_dependency
from db.models import Club, DepositFunnelEvent

router = APIRouter(
    prefix="/api/deposits/funnel",
    tags=["deposit-funnel"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_MIGRATION_HINT = (
    "Run: python migrate_deposit_funnel_events.py (or heroku run … on the web dyno)"
)

_STEP_LABELS: dict[str, str] = {
    "deposit_started": "/deposit started",
    "amount_entered": "Amount entered",
    "union_chosen": "Union chosen",
    "method_chosen": "Method chosen",
    "bind_setup_completed": "Bind setup completed",
    "instructions_sent": "Instructions sent",
    "payment_received": "Payment received",
    "payment_bound": "Payment bound",
    "chips_credited": "Chips credited",
    "chips_confirmed": "Chips confirmed",
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


def _raise_db_schema_error(exc: ProgrammingError) -> None:
    msg = str(exc.orig) if exc.orig else str(exc)
    if "deposit_funnel_events" in msg and "does not exist" in msg:
        raise HTTPException(503, f"Deposit funnel analytics table is missing. {_MIGRATION_HINT}")


def _show_union_step(club_id: int | None) -> bool:
    return club_id is not None and is_round_table_club(int(club_id))


def _funnel_events_query(
    db: Session,
    *,
    club_id: int | None,
    method_slug: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    is_first_deposit: bool | None,
    requires_method_setup: bool | None,
    step: str | None,
    exclude_test_chats: bool,
    session_ids: set[str] | None = None,
):
    q = db.query(DepositFunnelEvent)
    if club_id is not None:
        q = q.filter(DepositFunnelEvent.club_id == club_id)
    if from_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at >= from_dt)
    if to_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at <= to_dt)
    if is_first_deposit is not None:
        q = q.filter(DepositFunnelEvent.is_first_deposit == is_first_deposit)
    if requires_method_setup is not None:
        q = q.filter(
            DepositFunnelEvent.requires_method_setup == requires_method_setup
        )
    if step and step.strip():
        q = q.filter(DepositFunnelEvent.step == step.strip())
    if exclude_test_chats:
        q = apply_analytics_chat_exclusion(
            db, q, DepositFunnelEvent.telegram_chat_id
        )
    slug = (method_slug or "").strip().lower()
    if slug and slug != "all":
        matching_sessions = (
            db.query(DepositFunnelEvent.deposit_session_id)
            .filter(DepositFunnelEvent.method_slug == slug)
        )
        if club_id is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.club_id == club_id
            )
        if from_dt is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.created_at >= from_dt
            )
        if to_dt is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.created_at <= to_dt
            )
        if exclude_test_chats:
            matching_sessions = apply_analytics_chat_exclusion(
                db, matching_sessions, DepositFunnelEvent.telegram_chat_id
            )
        q = q.filter(
            DepositFunnelEvent.deposit_session_id.in_(matching_sessions.distinct())
        )
    if session_ids is not None:
        if not session_ids:
            q = q.filter(DepositFunnelEvent.id == -1)
        else:
            q = q.filter(DepositFunnelEvent.deposit_session_id.in_(session_ids))
    return q


def _union_breakdown(
    db: Session,
    *,
    club_id: int,
    from_dt: datetime | None,
    to_dt: datetime | None,
    exclude_test_chats: bool,
) -> DepositFunnelUnionBreakdown:
    q = db.query(
        DepositFunnelEvent.metadata_json,
        DepositFunnelEvent.deposit_session_id,
    ).filter(
        DepositFunnelEvent.club_id == int(club_id),
        DepositFunnelEvent.step == STEP_UNION_CHOSEN,
    )
    if from_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at >= from_dt)
    if to_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at <= to_dt)
    if exclude_test_chats:
        q = apply_analytics_chat_exclusion(
            db, q, DepositFunnelEvent.telegram_chat_id
        )
    rt_sessions: set[str] = set()
    at_sessions: set[str] = set()
    for meta, session_id in q.all():
        shorthand = ""
        if isinstance(meta, dict):
            shorthand = str(meta.get("union_shorthand") or "").strip().upper()
        sid = str(session_id)
        if shorthand == "RT":
            rt_sessions.add(sid)
        elif shorthand == "AT":
            at_sessions.add(sid)
    return DepositFunnelUnionBreakdown(
        round_table=len(rt_sessions),
        aces_table=len(at_sessions),
    )



def _metadata_auto_bound(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    if meta.get("auto_bound") is True:
        return True
    return str(meta.get("bound_via") or "").strip().lower() == "auto"


def _full_auto_e2e_session_ids(
    db: Session,
    *,
    club_id: int | None,
    method_slug: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    exclude_test_chats: bool,
) -> set[str]:
    """Sessions that completed full e2e auto-deposit (no bind setup)."""
    base_q = _funnel_events_query(
        db,
        club_id=club_id,
        method_slug=method_slug,
        from_dt=from_dt,
        to_dt=to_dt,
        is_first_deposit=None,
        requires_method_setup=None,
        step=None,
        exclude_test_chats=exclude_test_chats,
    )
    e2e_rows = base_q.filter(
        DepositFunnelEvent.step == STEP_CHIPS_CONFIRMED,
        DepositFunnelEvent.metadata_json["path"].as_string() == "e2e_auto_deposit",
    ).with_entities(DepositFunnelEvent.deposit_session_id).distinct().all()
    e2e_ids = {str(row[0]) for row in e2e_rows}
    if not e2e_ids:
        return set()

    bind_rows = (
        base_q.filter(
            DepositFunnelEvent.step == STEP_BIND_SETUP_COMPLETED,
            DepositFunnelEvent.deposit_session_id.in_(e2e_ids),
        )
        .with_entities(DepositFunnelEvent.deposit_session_id)
        .distinct()
        .all()
    )
    bind_ids = {str(row[0]) for row in bind_rows}
    candidates = e2e_ids - bind_ids
    if not candidates:
        return set()

    bound_rows = base_q.filter(
        DepositFunnelEvent.step == STEP_PAYMENT_BOUND,
        DepositFunnelEvent.deposit_session_id.in_(candidates),
    ).with_entities(
        DepositFunnelEvent.deposit_session_id,
        DepositFunnelEvent.metadata_json,
    ).all()
    auto_bound_ids: set[str] = set()
    for session_id, meta in bound_rows:
        if _metadata_auto_bound(meta):
            auto_bound_ids.add(str(session_id))
    return auto_bound_ids


def _step_counts_for_sessions(
    db: Session,
    session_ids: set[str],
    display_steps: tuple[str, ...],
) -> dict[str, int]:
    if not session_ids:
        return {step: 0 for step in display_steps}
    rows = (
        db.query(
            DepositFunnelEvent.step,
            func.count(func.distinct(DepositFunnelEvent.deposit_session_id)),
        )
        .filter(DepositFunnelEvent.deposit_session_id.in_(session_ids))
        .group_by(DepositFunnelEvent.step)
        .all()
    )
    counts = {str(step): int(count or 0) for step, count in rows}
    return {step: counts.get(step, 0) for step in display_steps}


def _compute_step_latencies(
    events: list[DepositFunnelEvent],
    display_steps: tuple[str, ...],
) -> dict[str, float | None]:
    """Average seconds from previous display step to each step."""
    by_session: dict[str, list[DepositFunnelEvent]] = defaultdict(list)
    for row in events:
        by_session[str(row.deposit_session_id)].append(row)

    latency_sums: dict[str, float] = defaultdict(float)
    latency_counts: dict[str, int] = defaultdict(int)

    for session_events in by_session.values():
        by_step: dict[str, datetime] = {}
        for row in session_events:
            step = str(row.step)
            if step not in display_steps:
                continue
            created = row.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            prev = by_step.get(step)
            if prev is None or created < prev:
                by_step[step] = created

        session_step_times = [
            (step, by_step[step]) for step in display_steps if step in by_step
        ]
        for i, (step, current_time) in enumerate(session_step_times):
            if i == 0:
                continue
            prev_time = session_step_times[i - 1][1]
            delta = (current_time - prev_time).total_seconds()
            if delta >= 0:
                latency_sums[step] += delta
                latency_counts[step] += 1

    result: dict[str, float | None] = {}
    for step in display_steps:
        if step == display_steps[0]:
            result[step] = None
        elif latency_counts[step]:
            result[step] = latency_sums[step] / latency_counts[step]
        else:
            result[step] = None
    return result


def _build_step_counts(
    counts_by_step: dict[str, int],
    display_steps: tuple[str, ...],
    started: int,
) -> list[DepositFunnelStepCount]:
    steps: list[DepositFunnelStepCount] = []
    for step in display_steps:
        count = counts_by_step.get(step, 0)
        conversion = (count / started) if started else None
        steps.append(
            DepositFunnelStepCount(
                step=step,
                label=_STEP_LABELS.get(step, step),
                count=count,
                conversion_rate=conversion,
            )
        )
    return steps


def _event_read(row: DepositFunnelEvent, club_name: str | None) -> DepositFunnelEventRead:
    amount_cents = int(row.amount_cents) if row.amount_cents is not None else None
    return DepositFunnelEventRead(
        id=int(row.id),
        deposit_session_id=str(row.deposit_session_id),
        step=str(row.step),
        club_id=int(row.club_id) if row.club_id is not None else None,
        club_name=club_name,
        telegram_user_id=(
            int(row.telegram_user_id) if row.telegram_user_id is not None else None
        ),
        telegram_chat_id=int(row.telegram_chat_id),
        method_slug=str(row.method_slug) if row.method_slug else None,
        amount_cents=amount_cents,
        amount_usd=cents_to_usd(amount_cents) if amount_cents is not None else None,
        is_first_deposit=bool(row.is_first_deposit),
        requires_method_setup=bool(row.requires_method_setup),
        metadata=row.metadata_json,
        created_at=row.created_at,
    )


@router.get("/summary", response_model=DepositFunnelSummaryResponse)
def deposit_funnel_summary(
    club_id: int | None = Query(None),
    method: str | None = Query(None, description="payment method slug filter"),
    is_first_deposit: bool | None = Query(None),
    requires_method_setup: bool | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    exclude_test_chats: bool = Query(True),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    show_union = _show_union_step(club_id)
    display_steps = display_funnel_step_order(show_union_step=show_union)
    try:
        base_q = _funnel_events_query(
            db,
            club_id=club_id,
            method_slug=method,
            from_dt=dt_from,
            to_dt=dt_to,
            is_first_deposit=is_first_deposit,
            requires_method_setup=requires_method_setup,
            step=None,
            exclude_test_chats=exclude_test_chats,
        )
        counts_by_step = {
            str(row[0]): int(row[1] or 0)
            for row in base_q.with_entities(
                DepositFunnelEvent.step,
                func.count(func.distinct(DepositFunnelEvent.deposit_session_id)),
            )
            .group_by(DepositFunnelEvent.step)
            .all()
        }
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    started = counts_by_step.get(STEP_DEPOSIT_STARTED, 0)
    steps = _build_step_counts(counts_by_step, display_steps, started)

    union_breakdown: DepositFunnelUnionBreakdown | None = None
    if show_union and club_id is not None:
        union_breakdown = _union_breakdown(
            db,
            club_id=int(club_id),
            from_dt=dt_from,
            to_dt=dt_to,
            exclude_test_chats=exclude_test_chats,
        )

    return DepositFunnelSummaryResponse(
        club_id=club_id,
        started=started,
        steps=steps,
        show_union_step=show_union,
        union_breakdown=union_breakdown,
    )


@router.get("/latency-summary", response_model=DepositFunnelLatencySummaryResponse)
def deposit_funnel_latency_summary(
    club_id: int | None = Query(None),
    method: str | None = Query(None, description="payment method slug filter"),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    exclude_test_chats: bool = Query(True),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    show_union = _show_union_step(club_id)
    display_steps = display_funnel_step_order(
        show_union_step=show_union,
        include_bind_setup=False,
    )
    try:
        session_ids = _full_auto_e2e_session_ids(
            db,
            club_id=club_id,
            method_slug=method,
            from_dt=dt_from,
            to_dt=dt_to,
            exclude_test_chats=exclude_test_chats,
        )
        counts_by_step = _step_counts_for_sessions(db, session_ids, display_steps)
        events = (
            db.query(DepositFunnelEvent)
            .filter(DepositFunnelEvent.deposit_session_id.in_(session_ids))
            .all()
            if session_ids
            else []
        )
        avg_latencies = _compute_step_latencies(events, display_steps)
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    started = counts_by_step.get(STEP_DEPOSIT_STARTED, 0)
    steps: list[DepositFunnelLatencyStep] = []
    for step in display_steps:
        count = counts_by_step.get(step, 0)
        conversion = (count / started) if started else None
        steps.append(
            DepositFunnelLatencyStep(
                step=step,
                label=_STEP_LABELS.get(step, step),
                count=count,
                conversion_rate=conversion,
                avg_latency_seconds=avg_latencies.get(step),
            )
        )

    union_breakdown: DepositFunnelUnionBreakdown | None = None
    if show_union and club_id is not None:
        union_breakdown = _union_breakdown(
            db,
            club_id=int(club_id),
            from_dt=dt_from,
            to_dt=dt_to,
            exclude_test_chats=exclude_test_chats,
        )

    return DepositFunnelLatencySummaryResponse(
        club_id=club_id,
        started=started,
        steps=steps,
        show_union_step=show_union,
        union_breakdown=union_breakdown,
    )


@router.get("/events", response_model=DepositFunnelEventListResponse)
def list_deposit_funnel_events(
    club_id: int | None = Query(None),
    method: str | None = Query(None),
    step: str | None = Query(None),
    is_first_deposit: bool | None = Query(None),
    requires_method_setup: bool | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    exclude_test_chats: bool = Query(True),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    limit = _clamp_limit(limit)
    try:
        q = _funnel_events_query(
            db,
            club_id=club_id,
            method_slug=method,
            from_dt=dt_from,
            to_dt=dt_to,
            is_first_deposit=is_first_deposit,
            requires_method_setup=requires_method_setup,
            step=step,
            exclude_test_chats=exclude_test_chats,
        )
        total = q.count()
        rows = (
            q.order_by(DepositFunnelEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    club_names: dict[int, str | None] = {}
    items: list[DepositFunnelEventRead] = []
    for row in rows:
        cid = int(row.club_id) if row.club_id is not None else None
        if cid is not None and cid not in club_names:
            club = db.query(Club).filter(Club.id == cid).first()
            club_names[cid] = club.name if club else None
        items.append(_event_read(row, club_names.get(cid) if cid else None))
    return DepositFunnelEventListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
