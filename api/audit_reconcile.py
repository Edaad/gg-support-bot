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
from api.club_slug import (
    CLUB_SLUG_TO_NAME,
    ROUND_TABLE_TRADE_SLUGS,
    is_round_table_composite,
    resolve_club_id,
)
from api.payments_helpers import lookup_gg_nickname
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
    EarlyRakebackLine,
    EarlyRakebackSnapshot,
    PlayerDetails,
    TradeRecordLine,
    TradeRecordUpload,
)

PlayerStatus = Literal["match", "mismatch", "trade_only", "ledger_only"]
RunStatus = Literal["pass", "fail", "blocked"]

# Pass when |net_trade_record − net_ledger| ≤ this amount (USD).
RECONCILE_MATCH_TOLERANCE_USD = Decimal("2")


def _within_match_tolerance(delta: Decimal) -> bool:
    return abs(delta) <= RECONCILE_MATCH_TOLERANCE_USD


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
    member_nickname: str | None
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
    trade_upload_ids: list[int] = field(default_factory=list)
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
) -> tuple[dict[str, Decimal], dict[str, int], list[UnmatchedTradeRow], dict[str, str]]:
    lines = (
        session.query(TradeRecordLine)
        .filter_by(upload_id=upload.id)
        .order_by(TradeRecordLine.sheet_row.asc())
        .all()
    )
    by_player: dict[str, Decimal] = {}
    row_counts: dict[str, int] = {}
    nicknames: dict[str, str] = {}
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
        nick = (line.member_nickname or "").strip()
        if nick and nick != gg_id and gg_id not in nicknames:
            nicknames[gg_id] = nick

    return by_player, row_counts, unmatched, nicknames


def aggregate_trade_records(
    session: Session,
    *,
    uploads: list[TradeRecordUpload],
) -> tuple[dict[str, Decimal], dict[str, int], list[UnmatchedTradeRow], dict[str, str]]:
    by_player: dict[str, Decimal] = {}
    row_counts: dict[str, int] = {}
    nicknames: dict[str, str] = {}
    unmatched: list[UnmatchedTradeRow] = []

    for upload in uploads:
        trade, counts, um, nicks = aggregate_trade_record(session, upload=upload)
        for gg_id, amount in trade.items():
            by_player[gg_id] = by_player.get(gg_id, Decimal(0)) + amount
            row_counts[gg_id] = row_counts.get(gg_id, 0) + counts.get(gg_id, 0)
        unmatched.extend(um)
        for gg_id, nick in nicks.items():
            if gg_id not in nicknames:
                nicknames[gg_id] = nick

    return by_player, row_counts, unmatched, nicknames


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


def _ledger_events_for_clubs(
    session: Session,
    *,
    club_slugs: list[str],
    audit_date: date,
    warnings: list[str],
) -> tuple[list[LedgerEvent], str | None]:
    """Collect ledger events for one or more slugs (Round Table composite)."""
    slugs = [s.strip().lower() for s in club_slugs]
    events: list[LedgerEvent] = []

    for slug in slugs:
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
            warnings.append(
                f"No early rakeback snapshot for {slug} on {audit_date}; early_rakeback = 0"
            )

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
            warnings.append(f"Non-Monday audit day for {slug}; monday_settlement = 0")

    glide_slug = "round-table" if "round-table" in slugs else slugs[0]
    try:
        glide_events, glide_warnings = fetch_glide_ledger_events(
            club_slug=glide_slug,
            audit_date=audit_date,
            existing_events=events,
        )
        events.extend(glide_events)
        warnings.extend(glide_warnings)
    except GlideConfigError as exc:
        return events, str(exc)

    return events, None


def _trade_nicknames_from_upload(
    session: Session,
    upload_id: int | None,
) -> dict[str, str]:
    if not upload_id:
        return {}
    lines = (
        session.query(TradeRecordLine)
        .filter_by(upload_id=int(upload_id))
        .order_by(TradeRecordLine.sheet_row.asc())
        .all()
    )
    nicknames: dict[str, str] = {}
    for line in lines:
        gg_id = (line.member_gg_player_id or "").strip()
        nick = (line.member_nickname or "").strip()
        if gg_id and nick and nick != gg_id and gg_id not in nicknames:
            nicknames[gg_id] = nick
    return nicknames


def _early_rb_nicknames_for_club_date(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> dict[str, str]:
    snapshot = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=club_slug.strip().lower(), audit_date=audit_date)
        .first()
    )
    if snapshot is None:
        return {}
    rows = (
        session.query(EarlyRakebackLine)
        .filter_by(snapshot_id=int(snapshot.id))
        .all()
    )
    nicknames: dict[str, str] = {}
    for line in rows:
        gid = (line.gg_player_id or "").strip()
        name = (line.member_nickname or "").strip()
        if gid and name and gid not in nicknames:
            nicknames[gid] = name
    return nicknames


def _player_details_nicknames(
    session: Session,
    *,
    club_id: int,
    gg_ids: set[str],
) -> dict[str, str]:
    if not gg_ids:
        return {}
    rows = (
        session.query(PlayerDetails)
        .filter(
            PlayerDetails.club_id == int(club_id),
            PlayerDetails.gg_player_id.in_(sorted(gg_ids)),
        )
        .all()
    )
    nicknames: dict[str, str] = {}
    for row in rows:
        gid = (row.gg_player_id or "").strip()
        name = (row.gg_nickname or "").strip()
        if gid and name and name != gid and gid not in nicknames:
            nicknames[gid] = name
    return nicknames


def _trade_nicknames_from_uploads(
    session: Session,
    upload_ids: list[int],
) -> dict[str, str]:
    nicknames: dict[str, str] = {}
    for upload_id in upload_ids:
        nicknames.update(_trade_nicknames_from_upload(session, upload_id))
    return nicknames


def _nicknames_for_reconcile(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
    trade_nicknames: dict[str, str],
    gg_ids: set[str],
    trade_upload_id: int | None = None,
    trade_upload_ids: list[int] | None = None,
) -> dict[str, str | None]:
    slug = club_slug.strip().lower()
    club_id = resolve_club_id(session, slug)

    merged: dict[str, str] = {}
    if trade_upload_ids:
        merged.update(_trade_nicknames_from_uploads(session, trade_upload_ids))
    merged.update(_trade_nicknames_from_upload(session, trade_upload_id))
    merged.update(trade_nicknames)
    if is_round_table_composite(slug):
        for trade_slug in ROUND_TABLE_TRADE_SLUGS:
            merged.update(
                _early_rb_nicknames_for_club_date(
                    session, club_slug=trade_slug, audit_date=audit_date
                )
            )
    else:
        merged.update(
            _early_rb_nicknames_for_club_date(
                session, club_slug=slug, audit_date=audit_date
            )
        )
    merged.update(
        _player_details_nicknames(session, club_id=club_id, gg_ids=gg_ids)
    )

    result: dict[str, str | None] = {}
    for gg_id in gg_ids:
        nick = merged.get(gg_id)
        if not nick:
            fallback = lookup_gg_nickname(session, club_id, gg_id)
            if fallback and fallback.strip() != gg_id:
                nick = fallback
        result[gg_id] = (nick or "").strip() or None
    return result


def enrich_report_nicknames(
    session: Session,
    report: AuditReconcileReport,
) -> None:
    """Fill missing player nicknames on stored or freshly built reports."""
    missing = {p.gg_player_id for p in report.players if not p.member_nickname}
    if not missing:
        return
    nicknames = _nicknames_for_reconcile(
        session,
        club_slug=report.club_slug,
        audit_date=report.audit_date,
        trade_nicknames={},
        gg_ids=missing,
        trade_upload_id=report.trade_upload_id,
        trade_upload_ids=report.trade_upload_ids or None,
    )
    for player in report.players:
        if not player.member_nickname:
            player.member_nickname = nicknames.get(player.gg_player_id)


def _compare_players(
    trade_by_player: dict[str, Decimal],
    ledger_by_player: dict[str, LedgerBreakdown],
    nicknames_by_player: dict[str, str | None],
) -> list[AuditReconcilePlayerResult]:
    all_ids = sorted(set(trade_by_player) | set(ledger_by_player))
    results: list[AuditReconcilePlayerResult] = []

    for gg_id in all_ids:
        net_trade = trade_by_player.get(gg_id, Decimal(0))
        breakdown = ledger_by_player.get(gg_id, LedgerBreakdown())
        net_ledger = breakdown.net
        delta = net_trade - net_ledger

        if gg_id in trade_by_player and gg_id in ledger_by_player:
            status: PlayerStatus = (
                "match" if _within_match_tolerance(delta) else "mismatch"
            )
        elif gg_id in trade_by_player:
            status = "trade_only"
        else:
            status = "ledger_only"

        results.append(
            AuditReconcilePlayerResult(
                gg_player_id=gg_id,
                member_nickname=nicknames_by_player.get(gg_id),
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
        "trade_upload_ids": report.trade_upload_ids,
        "early_rb_snapshot_id": report.early_rb_snapshot_id,
        "players": [
            {
                "gg_player_id": p.gg_player_id,
                "member_nickname": p.member_nickname,
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


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _ledger_breakdown_from_dict(raw: dict[str, Any]) -> LedgerBreakdown:
    return LedgerBreakdown(
        deposits=_decimal(raw.get("deposits", 0)),
        early_rb=_decimal(raw.get("early_rb", 0)),
        bonuses=_decimal(raw.get("bonuses", 0)),
        monday=_decimal(raw.get("monday", 0)),
        glide=_decimal(raw.get("glide", 0)),
        cashouts=_decimal(raw.get("cashouts", 0)),
    )


def report_from_json(raw: str, *, run_id: int | None = None) -> AuditReconcileReport:
    """Deserialize a persisted reconcile report_json payload."""
    data = json.loads(raw)
    players = [
        AuditReconcilePlayerResult(
            gg_player_id=str(p["gg_player_id"]),
            member_nickname=p.get("member_nickname"),
            net_trade_record=_decimal(p["net_trade_record"]),
            net_ledger=_decimal(p["net_ledger"]),
            delta=_decimal(p["delta"]),
            ledger_breakdown=_ledger_breakdown_from_dict(p.get("ledger_breakdown") or {}),
            status=p["status"],
        )
        for p in data.get("players") or []
    ]
    unmatched_trade = [
        UnmatchedTradeRow(
            line_id=int(u["line_id"]),
            amount=_decimal(u["amount"]),
            member_nickname=u.get("member_nickname"),
            sheet_row=int(u["sheet_row"]),
        )
        for u in data.get("unmatched_trade") or []
    ]
    unmatched_ledger = [
        UnmatchedLedgerEvent(
            source=str(u["source"]),
            amount_usd=_decimal(u["amount_usd"]),
            external_id=str(u["external_id"]),
            detail=u.get("detail"),
        )
        for u in data.get("unmatched_ledger") or []
    ]
    matched = [p for p in players if p.status == "match"]
    failed = [p for p in players if p.status == "mismatch"]
    return AuditReconcileReport(
        audit_date=date.fromisoformat(str(data["audit_date"])[:10]),
        club_slug=str(data["club_slug"]),
        club_name=str(data.get("club_name") or CLUB_SLUG_TO_NAME.get(data["club_slug"], data["club_slug"])),
        status=data["status"],
        run_id=run_id,
        trade_upload_id=data.get("trade_upload_id"),
        trade_upload_ids=list(data.get("trade_upload_ids") or []),
        early_rb_snapshot_id=data.get("early_rb_snapshot_id"),
        players=players,
        unmatched_trade=unmatched_trade,
        unmatched_ledger=unmatched_ledger,
        warnings=list(data.get("warnings") or []),
        blocked_reason=data.get("blocked_reason"),
        players_matched=len(matched),
        players_failed=len(failed),
        unmatched_trade_count=len(unmatched_trade),
        unmatched_ledger_count=len(unmatched_ledger),
    )


def load_stored_reconcile_report(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> AuditReconcileReport | None:
    slug = club_slug.strip().lower()
    run = (
        session.query(AuditReconcileRun)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )
    if run is None or not run.report_json:
        return None
    report = report_from_json(run.report_json, run_id=int(run.id))
    report.players_matched = int(run.players_matched or report.players_matched)
    report.players_failed = int(run.players_failed or report.players_failed)
    report.unmatched_trade_count = int(
        run.unmatched_trade_count or report.unmatched_trade_count
    )
    report.unmatched_ledger_count = int(
        run.unmatched_ledger_count or report.unmatched_ledger_count
    )
    enrich_report_nicknames(session, report)
    return report


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

    if is_round_table_composite(slug):
        uploads: list[TradeRecordUpload] = []
        for trade_slug in ROUND_TABLE_TRADE_SLUGS:
            row = (
                session.query(TradeRecordUpload)
                .filter_by(club_slug=trade_slug, audit_date=audit_date)
                .first()
            )
            if row is None:
                report = AuditReconcileReport(
                    audit_date=audit_date,
                    club_slug=slug,
                    club_name=club_name,
                    status="blocked",
                    blocked_reason=(
                        f"Missing trade record upload for {trade_slug} on {audit_date.isoformat()}"
                    ),
                )
                if persist:
                    club_id = resolve_club_id(session, slug)
                    _replace_run(session, report=report, club_id=club_id)
                return report
            uploads.append(row)
        trade_upload_ids = [int(u.id) for u in uploads]
        rt_upload = uploads[0]
        trade_by_player, _row_counts, unmatched_trade, trade_nicknames = (
            aggregate_trade_records(session, uploads=uploads)
        )
        ledger_events, blocked_reason = _ledger_events_for_clubs(
            session,
            club_slugs=list(ROUND_TABLE_TRADE_SLUGS),
            audit_date=audit_date,
            warnings=warnings,
        )
        primary_upload_id = int(rt_upload.id)
    else:
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

        trade_upload_ids = [int(upload.id)]
        primary_upload_id = int(upload.id)
        trade_by_player, _row_counts, unmatched_trade, trade_nicknames = (
            aggregate_trade_record(session, upload=upload)
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
            trade_upload_id=primary_upload_id,
            trade_upload_ids=trade_upload_ids,
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

    players = _compare_players(
        trade_by_player,
        ledger_by_player,
        _nicknames_for_reconcile(
            session,
            club_slug=slug,
            audit_date=audit_date,
            trade_nicknames=trade_nicknames,
            gg_ids=set(trade_by_player) | set(ledger_by_player),
            trade_upload_id=primary_upload_id,
            trade_upload_ids=trade_upload_ids,
        ),
    )
    mismatches = [p for p in players if p.status == "mismatch"]
    matched = [p for p in players if p.status == "match"]

    snapshot = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )
    if snapshot is None and is_round_table_composite(slug):
        snapshot = (
            session.query(EarlyRakebackSnapshot)
            .filter_by(club_slug="round-table", audit_date=audit_date)
            .first()
        )

    status: RunStatus = "fail" if mismatches else "pass"

    report = AuditReconcileReport(
        audit_date=audit_date,
        club_slug=slug,
        club_name=club_name,
        status=status,
        trade_upload_id=primary_upload_id,
        trade_upload_ids=trade_upload_ids,
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
    enrich_report_nicknames(session, report)

    if persist:
        club_id = resolve_club_id(session, slug)
        _replace_run(session, report=report, club_id=club_id)

    return report
