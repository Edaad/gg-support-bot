"""Net reconcile engine: trade record vs internal ledger per club + audit day."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.orm import Session

from api.audit_ledger import (
    LedgerBreakdown,
    LedgerEvent,
    aggregate_ledger_by_player,
    fetch_bonus_events,
    fetch_cashout_events,
    fetch_deposit_events,
    fetch_early_rakeback_events,
)
from api.club_slug import CLUB_SLUG_TO_NAME, resolve_club_id
from api.glide_audit_sync import (
    GlideConfigError,
    fetch_glide_ledger_events,
)
from api.gg_computer_settlement import (
    SettlementFetchError,
    fetch_settlement_events,
    is_monday_audit_date,
)
from db.models import (
    AuditReconcileRun,
    EarlyRakebackSnapshot,
    TradeRecordLine,
    TradeRecordUpload,
)

PlayerStatus = Literal["match", "mismatch", "trade_only", "ledger_only"]
RunStatus = Literal["pass", "fail", "blocked"]


@dataclass
class UnmatchedTradeRow:
    line_id: int
    amount: Decimal
    member_nickname: str | None
    sheet_row: int


@dataclass
class UnmatchedLedgerEvent:
    source: str
    amount_usd: Decimal
    external_id: str
    detail: str | None = None


@dataclass
class AuditReconcilePlayerResult:
    gg_player_id: str
    net_trade_record: Decimal
    net_ledger: Decimal
    delta: Decimal
    ledger_breakdown: LedgerBreakdown
    status: PlayerStatus


@dataclass
class AuditReconcileReport:
    audit_date: date
    club_slug: str
    club_name: str
    status: RunStatus
    run_id: int | None = None
    trade_upload_id: int | None = None
    early_rb_snapshot_id: int | None = None
    players: list[AuditReconcilePlayerResult] = field(default_factory=list)
    unmatched_trade: list[UnmatchedTradeRow] = field(default_factory=list)
    unmatched_ledger: list[UnmatchedLedgerEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    players_matched: int = 0
    players_failed: int = 0
    unmatched_trade_count: int = 0
    unmatched_ledger_count: int = 0


def aggregate_trade_record(
    session: Session,
    *,
    upload: TradeRecordUpload,
) -> tuple[dict[str, Decimal], dict[str, int], list[UnmatchedTradeRow]]:
    lines = (
        session.query(TradeRecordLine)
        .filter_by(upload_id=upload.id)
        .order_by(TradeRecordLine.sheet_row.asc())
        .all()
    )
    by_player: dict[str, Decimal] = {}
    row_counts: dict[str, int] = {}
    unmatched: list[UnmatchedTradeRow] = []

    for line in lines:
        amount = Decimal(str(line.amount))
        if amount == 0:
            continue
        gg_id = (line.member_gg_player_id or "").strip()
        if not gg_id:
            unmatched.append(
                UnmatchedTradeRow(
                    line_id=int(line.id),
                    amount=amount,
                    member_nickname=line.member_nickname,
                    sheet_row=int(line.sheet_row),
                )
            )
            continue
        by_player[gg_id] = by_player.get(gg_id, Decimal(0)) + amount
        row_counts[gg_id] = row_counts.get(gg_id, 0) + 1

    return by_player, row_counts, unmatched


def _ledger_events_for_club(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
    warnings: list[str],
) -> tuple[list[LedgerEvent], str | None]:
    """Collect all ledger events; return (events, blocked_reason)."""
    slug = club_slug.strip().lower()
    events: list[LedgerEvent] = []
    events.extend(fetch_deposit_events(session, club_slug=slug, audit_date=audit_date))
    events.extend(
        fetch_early_rakeback_events(session, club_slug=slug, audit_date=audit_date)
    )
    events.extend(fetch_bonus_events(session, club_slug=slug, audit_date=audit_date))
    events.extend(fetch_cashout_events(session, club_slug=slug, audit_date=audit_date))

    snapshot = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )
    if snapshot is None:
        warnings.append("No early rakeback snapshot for this club/date; early_rakeback = 0")

    if is_monday_audit_date(slug, audit_date):
        try:
            settlement_events, settlement_warnings = fetch_settlement_events(
                club_slug=slug, audit_date=audit_date
            )
            events.extend(settlement_events)
            warnings.extend(settlement_warnings)
        except SettlementFetchError as exc:
            return events, str(exc)
    else:
        warnings.append("Non-Monday audit day; monday_settlement = 0")

    try:
        glide_events, glide_warnings = fetch_glide_ledger_events(
            club_slug=slug,
            audit_date=audit_date,
            existing_events=events,
        )
        events.extend(glide_events)
        warnings.extend(glide_warnings)
    except GlideConfigError as exc:
        return events, str(exc)

    return events, None


def _compare_players(
    trade_by_player: dict[str, Decimal],
    ledger_by_player: dict[str, LedgerBreakdown],
) -> list[AuditReconcilePlayerResult]:
    all_ids = sorted(set(trade_by_player) | set(ledger_by_player))
    results: list[AuditReconcilePlayerResult] = []

    for gg_id in all_ids:
        net_trade = trade_by_player.get(gg_id, Decimal(0))
        breakdown = ledger_by_player.get(gg_id, LedgerBreakdown())
        net_ledger = breakdown.net
        delta = net_trade - net_ledger

        if gg_id in trade_by_player and gg_id in ledger_by_player:
            status: PlayerStatus = "match" if delta == 0 else "mismatch"
        elif gg_id in trade_by_player:
            status = "trade_only"
        else:
            status = "ledger_only"

        results.append(
            AuditReconcilePlayerResult(
                gg_player_id=gg_id,
                net_trade_record=net_trade,
                net_ledger=net_ledger,
                delta=delta,
                ledger_breakdown=breakdown,
                status=status,
            )
        )
    return results


def _report_to_json(report: AuditReconcileReport) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, LedgerBreakdown):
            return asdict(obj)
        raise TypeError(type(obj))

    payload = {
        "audit_date": report.audit_date.isoformat(),
        "club_slug": report.club_slug,
        "club_name": report.club_name,
        "status": report.status,
        "trade_upload_id": report.trade_upload_id,
        "early_rb_snapshot_id": report.early_rb_snapshot_id,
        "players": [
            {
                "gg_player_id": p.gg_player_id,
                "net_trade_record": str(p.net_trade_record),
                "net_ledger": str(p.net_ledger),
                "delta": str(p.delta),
                "ledger_breakdown": asdict(p.ledger_breakdown),
                "status": p.status,
            }
            for p in report.players
        ],
        "unmatched_trade": [asdict(u) for u in report.unmatched_trade],
        "unmatched_ledger": [asdict(u) for u in report.unmatched_ledger],
        "warnings": report.warnings,
        "blocked_reason": report.blocked_reason,
    }
    return json.dumps(payload, default=_default)


def _replace_run(
    session: Session,
    *,
    report: AuditReconcileReport,
    club_id: int,
) -> AuditReconcileRun:
    existing = (
        session.query(AuditReconcileRun)
        .filter_by(club_slug=report.club_slug, audit_date=report.audit_date)
        .first()
    )
    if existing:
        run = existing
    else:
        run = AuditReconcileRun(
            club_slug=report.club_slug,
            audit_date=report.audit_date,
        )
        session.add(run)

    run.club_id = club_id
    run.status = report.status
    run.trade_upload_id = report.trade_upload_id
    run.early_rb_snapshot_id = report.early_rb_snapshot_id
    run.players_matched = report.players_matched
    run.players_failed = report.players_failed
    run.unmatched_trade_count = report.unmatched_trade_count
    run.unmatched_ledger_count = report.unmatched_ledger_count
    run.report_json = _report_to_json(report)
    session.flush()
    report.run_id = int(run.id)
    return run


def run_audit_reconcile(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
    persist: bool = True,
) -> AuditReconcileReport:
    slug = club_slug.strip().lower()
    club_name = CLUB_SLUG_TO_NAME.get(slug, slug)
    warnings: list[str] = []

    upload = (
        session.query(TradeRecordUpload)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )
    if upload is None:
        report = AuditReconcileReport(
            audit_date=audit_date,
            club_slug=slug,
            club_name=club_name,
            status="blocked",
            blocked_reason="No trade record upload for this club and audit date",
        )
        if persist:
            club_id = resolve_club_id(session, slug)
            _replace_run(session, report=report, club_id=club_id)
        return report

    trade_by_player, _row_counts, unmatched_trade = aggregate_trade_record(
        session, upload=upload
    )

    ledger_events, blocked_reason = _ledger_events_for_club(
        session, club_slug=slug, audit_date=audit_date, warnings=warnings
    )
    if blocked_reason:
        report = AuditReconcileReport(
            audit_date=audit_date,
            club_slug=slug,
            club_name=club_name,
            status="blocked",
            trade_upload_id=int(upload.id),
            unmatched_trade=unmatched_trade,
            unmatched_trade_count=len(unmatched_trade),
            warnings=warnings,
            blocked_reason=blocked_reason,
        )
        if persist:
            club_id = resolve_club_id(session, slug)
            _replace_run(session, report=report, club_id=club_id)
        return report

    ledger_by_player, unmatched_ledger_events = aggregate_ledger_by_player(
        ledger_events
    )
    unmatched_ledger = [
        UnmatchedLedgerEvent(
            source=e.source,
            amount_usd=e.amount_usd,
            external_id=e.external_id,
            detail=e.detail,
        )
        for e in unmatched_ledger_events
    ]

    players = _compare_players(trade_by_player, ledger_by_player)
    mismatches = [p for p in players if p.status == "mismatch"]
    matched = [p for p in players if p.status == "match"]

    snapshot = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )

    status: RunStatus = "fail" if mismatches else "pass"

    report = AuditReconcileReport(
        audit_date=audit_date,
        club_slug=slug,
        club_name=club_name,
        status=status,
        trade_upload_id=int(upload.id),
        early_rb_snapshot_id=int(snapshot.id) if snapshot else None,
        players=players,
        unmatched_trade=unmatched_trade,
        unmatched_ledger=unmatched_ledger,
        warnings=warnings,
        players_matched=len(matched),
        players_failed=len(mismatches),
        unmatched_trade_count=len(unmatched_trade),
        unmatched_ledger_count=len(unmatched_ledger),
    )

    if persist:
        club_id = resolve_club_id(session, slug)
        _replace_run(session, report=report, club_id=club_id)

    return report
