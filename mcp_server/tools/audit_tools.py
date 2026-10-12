"""Audit tools: reconcile runs/reports, trade-record uploads, early-rakeback snapshots.

Audit tables key on the gg-computer club *slug* (clubgto|round-table|aces-table|
creator-club). The ``club`` parameter accepts a slug directly or any other club
reference, which is mapped to the slug of the resolved club's name.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.audit_reconcile import load_stored_reconcile_report
from api.club_audit_timezone import (
    AuditTimezonePolicy,
    audit_timezone_label,
)
from api.club_slug import CLUB_SLUG_TO_NAME, slug_for_club_name
from api.early_rakeback_sync import parse_skipped_nicknames_json
from db.models import (
    AuditReconcileRun,
    EarlyRakebackSnapshot,
    TradeRecordLine,
    TradeRecordUpload,
)
from mcp_server.dates import parse_et_day
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

AUDIT_CLUB_DOC = (
    "Club slug (clubgto|round-table|aces-table|creator-club) or any club reference "
    "(id, key, shorthand); non-slug references map to the club's primary slug "
    "(Round Table -> round-table). Omit for all clubs."
)
_AUDIT_DAY_NOTE = "audit_date is the club's local audit calendar day"


def audit_slug_for(session: Session, club: str | None) -> str | None:
    """Resolve ``club`` to an audit slug; ``None`` when not given."""
    if club is None or not str(club).strip():
        return None
    lowered = str(club).strip().lower()
    if lowered in CLUB_SLUG_TO_NAME:
        return lowered
    row = resolve_club(session, club)
    if row is None:
        return None
    slug = slug_for_club_name(str(row.name or ""))
    if slug is None:
        raise bad_input(
            f"club {club!r} has no audit slug; use one of "
            + "|".join(CLUB_SLUG_TO_NAME)
        )
    return slug


def _slug_for_row(slug: str | None, club_id: int | None, names: dict[int, str]) -> str:
    if slug:
        return str(slug)
    if club_id is not None:
        mapped = slug_for_club_name(names.get(int(club_id), ""))
        if mapped:
            return mapped
    return ""


def _club_label(slug: str) -> str:
    return CLUB_SLUG_TO_NAME.get(slug, slug)


@translate_errors
def list_reconcile_runs(
    club: str | None = None,
    audit_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Stored audit reconcile runs (one per club slug + local audit day, newest first)
    with status and match/unmatched counts. The full report is large; fetch one
    with get_reconcile_report(club, audit_date). audit_date is YYYY-MM-DD.
    Club: Club slug (clubgto|round-table|aces-table|creator-club) or any club reference (id, key, shorthand); non-slug references map to the club's primary slug (Round Table -> round-table). Omit for all clubs."""
    day = parse_et_day(audit_date)
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        slug = audit_slug_for(session, club)
        query = session.query(AuditReconcileRun)
        if slug is not None:
            query = query.filter(AuditReconcileRun.club_slug == slug)
        if day is not None:
            query = query.filter(AuditReconcileRun.audit_date == day)
        total = query.order_by(None).count()
        rows = (
            query.order_by(
                AuditReconcileRun.created_at.desc(), AuditReconcileRun.id.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = []
        for run in rows:
            run_slug = _slug_for_row(run.club_slug, run.club_id, names)
            items.append(
                {
                    "id": int(run.id),
                    "club_id": int(run.club_id) if run.club_id is not None else None,
                    "club_slug": run_slug,
                    "club_name": _club_label(run_slug),
                    "audit_date": run.audit_date,
                    "status": run.status,
                    "players_matched": int(run.players_matched or 0),
                    "players_failed": int(run.players_failed or 0),
                    "unmatched_trade_count": int(run.unmatched_trade_count or 0),
                    "unmatched_ledger_count": int(run.unmatched_ledger_count or 0),
                    "trade_upload_id": run.trade_upload_id,
                    "early_rb_snapshot_id": run.early_rb_snapshot_id,
                    "created_at": run.created_at,
                }
            )
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        notes=[_AUDIT_DAY_NOTE, "report_json omitted; use get_reconcile_report"],
    )


@translate_errors
def get_reconcile_report(club: str, audit_date: str) -> dict[str, Any]:
    """The stored reconcile report for one club and local audit day: per-player
    trade-record vs ledger nets with delta and status, unmatched trade rows,
    unmatched ledger events, matching lines and warnings. Read-only: never runs a
    new reconcile. Amounts are dollars. Club: Club slug (clubgto|round-table|aces-table|creator-club) or any club reference (id, key, shorthand); non-slug references map to the club's primary slug (Round Table -> round-table). Omit for all clubs."""
    day = parse_et_day(audit_date)
    if day is None:
        raise bad_input("audit_date (YYYY-MM-DD) is required")
    with read_session() as session:
        slug = audit_slug_for(session, club)
        if slug is None:
            raise bad_input("club is required")
        report = load_stored_reconcile_report(session, club_slug=slug, audit_date=day)
        if report is None:
            raise not_found(
                f"no stored reconcile report for {slug} on {day.isoformat()}; "
                "an operator must run the reconcile on the Audit page first"
            )
    return single(
        report,
        notes=[_AUDIT_DAY_NOTE, "amounts are dollars", "stored result; not recomputed"],
    )


@translate_errors
def list_trade_record_uploads(
    club: str | None = None,
    audit_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Trade Record XLSX uploads (one per club slug + local audit day, newest first)
    with filename, timezone policy and parsed transaction line count.
    audit_date is YYYY-MM-DD. Club: Club slug (clubgto|round-table|aces-table|creator-club) or any club reference (id, key, shorthand); non-slug references map to the club's primary slug (Round Table -> round-table). Omit for all clubs."""
    day = parse_et_day(audit_date)
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        slug = audit_slug_for(session, club)
        base = session.query(TradeRecordUpload)
        if slug is not None:
            base = base.filter(TradeRecordUpload.club_slug == slug)
        if day is not None:
            base = base.filter(TradeRecordUpload.audit_date == day)
        total = base.order_by(None).count()
        query = session.query(
            TradeRecordUpload, func.count(TradeRecordLine.id).label("line_count")
        ).outerjoin(TradeRecordLine, TradeRecordLine.upload_id == TradeRecordUpload.id)
        if slug is not None:
            query = query.filter(TradeRecordUpload.club_slug == slug)
        if day is not None:
            query = query.filter(TradeRecordUpload.audit_date == day)
        rows = (
            query.group_by(TradeRecordUpload.id)
            .order_by(TradeRecordUpload.created_at.desc(), TradeRecordUpload.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = []
        for upload, line_count in rows:
            up_slug = _slug_for_row(upload.club_slug, upload.club_id, names)
            policy_value = upload.audit_timezone_policy
            tz_label = None
            if policy_value:
                try:
                    tz_label = audit_timezone_label(AuditTimezonePolicy(policy_value))
                except ValueError:
                    tz_label = None
            items.append(
                {
                    "id": int(upload.id),
                    "club_id": int(upload.club_id)
                    if upload.club_id is not None
                    else None,
                    "club_slug": up_slug,
                    "club_name": _club_label(up_slug),
                    "audit_date": upload.audit_date,
                    "audit_timezone_policy": policy_value,
                    "audit_timezone_label": tz_label,
                    "filename": upload.filename,
                    "transaction_count": int(line_count or 0),
                    "replaced_upload_id": upload.replaced_upload_id,
                    "created_at": upload.created_at,
                }
            )
    return page(items, total=total, limit=limit, offset=offset, notes=[_AUDIT_DAY_NOTE])


@translate_errors
def list_early_rakeback_snapshots(
    club: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Early-rakeback sync snapshots (one per club slug + local audit day, most
    recently synced first): lines fetched/stored/skipped and the unmapped
    nicknames that were skipped. from/to are inclusive YYYY-MM-DD audit days.
    Club: Club slug (clubgto|round-table|aces-table|creator-club) or any club reference (id, key, shorthand); non-slug references map to the club's primary slug (Round Table -> round-table). Omit for all clubs."""
    start = parse_et_day(from_)
    stop = parse_et_day(to)
    if start and stop and start > stop:
        raise bad_input("from must be on or before to")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        slug = audit_slug_for(session, club)
        query = session.query(EarlyRakebackSnapshot)
        if slug is not None:
            query = query.filter(EarlyRakebackSnapshot.club_slug == slug)
        if start is not None:
            query = query.filter(EarlyRakebackSnapshot.audit_date >= start)
        if stop is not None:
            query = query.filter(EarlyRakebackSnapshot.audit_date <= stop)
        total = query.order_by(None).count()
        rows = (
            query.order_by(
                EarlyRakebackSnapshot.synced_at.desc(), EarlyRakebackSnapshot.id.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = []
        for snap in rows:
            snap_slug = _slug_for_row(snap.club_slug, snap.club_id, names)
            skips = parse_skipped_nicknames_json(snap.skipped_nicknames)
            items.append(
                {
                    "id": int(snap.id),
                    "club_id": int(snap.club_id) if snap.club_id is not None else None,
                    "club_slug": snap_slug,
                    "club_name": _club_label(snap_slug),
                    "audit_date": snap.audit_date,
                    "fetch_from_utc": snap.fetch_from_utc,
                    "fetch_to_utc": snap.fetch_to_utc,
                    "lines_fetched": int(snap.lines_fetched or 0),
                    "lines_stored": int(snap.lines_stored or 0),
                    "lines_skipped_unmapped": int(snap.lines_skipped_unmapped or 0),
                    "skips": [
                        {
                            "nickname": skip.nickname,
                            "reason": skip.reason,
                            "count": skip.count,
                            "reason_label": skip.reason_label,
                        }
                        for skip in skips
                    ],
                    "synced_at": snap.synced_at,
                }
            )
    return page(items, total=total, limit=limit, offset=offset, notes=[_AUDIT_DAY_NOTE])


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_reconcile_runs", annotations=READ_ONLY)(list_reconcile_runs)
    mcp.tool(name="get_reconcile_report", annotations=READ_ONLY)(get_reconcile_report)
    mcp.tool(name="list_trade_record_uploads", annotations=READ_ONLY)(
        list_trade_record_uploads
    )
    mcp.tool(name="list_early_rakeback_snapshots", annotations=READ_ONLY)(
        list_early_rakeback_snapshots
    )
