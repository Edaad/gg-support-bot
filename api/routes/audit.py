"""Audit trade record upload API."""

from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.club_audit_timezone import (
    AuditTimezonePolicy,
    audit_timezone_for_slug,
    audit_timezone_label,
)
from api.club_slug import CLUB_SLUG_TO_NAME, resolve_club_id, slug_for_club_id
from api.schemas_audit import (
    AuditReconcileReportSchema,
    AuditReconcileRunSummary,
    EarlyRakebackSnapshotSummary,
    EarlyRakebackSyncReport,
    TradeRecordUploadReport,
    TradeRecordUploadSummary,
)
from api.trade_record_parser import (
    TradeRecordParseError,
    TradeRecordValidationError,
    parse_trade_record_workbook,
)
from api.trade_record_sync import sync_identities
from api.aon_beta_client import AonBetaConfigError
from api.early_rakeback_sync import sync_early_rakeback_for_date
from api.audit_reconcile import (
    AuditReconcileReport,
    load_stored_reconcile_report,
    run_audit_reconcile,
)
from api.audit_reconcile_export import build_reconcile_workbook_from_report
from db.connection import get_db_dependency
from db.models import AuditReconcileRun, EarlyRakebackSnapshot, TradeRecordLine, TradeRecordUpload

router = APIRouter(
    prefix="/api/audit",
    tags=["audit"],
    dependencies=[Depends(get_current_admin)],
)

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _parse_audit_date(raw: str) -> date:
    text = (raw or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid audit_date: {raw!r}") from exc


def _report_to_schema(report: AuditReconcileReport) -> AuditReconcileReportSchema:
    return AuditReconcileReportSchema(
        audit_date=report.audit_date,
        club_slug=report.club_slug,
        club_name=report.club_name,
        status=report.status,
        run_id=report.run_id,
        trade_upload_id=report.trade_upload_id,
        early_rb_snapshot_id=report.early_rb_snapshot_id,
        players=[
            {
                "gg_player_id": p.gg_player_id,
                "member_nickname": p.member_nickname,
                "net_trade_record": str(p.net_trade_record),
                "net_ledger": str(p.net_ledger),
                "delta": str(p.delta),
                "ledger_breakdown": {
                    "deposits": str(p.ledger_breakdown.deposits),
                    "early_rb": str(p.ledger_breakdown.early_rb),
                    "bonuses": str(p.ledger_breakdown.bonuses),
                    "monday": str(p.ledger_breakdown.monday),
                    "glide": str(p.ledger_breakdown.glide),
                    "cashouts": str(p.ledger_breakdown.cashouts),
                },
                "status": p.status,
            }
            for p in report.players
        ],
        unmatched_trade=[
            {
                "line_id": u.line_id,
                "amount": str(u.amount),
                "member_nickname": u.member_nickname,
                "sheet_row": u.sheet_row,
            }
            for u in report.unmatched_trade
        ],
        unmatched_ledger=[
            {
                "source": u.source,
                "amount_usd": str(u.amount_usd),
                "external_id": u.external_id,
                "detail": u.detail,
            }
            for u in report.unmatched_ledger
        ],
        warnings=report.warnings,
        blocked_reason=report.blocked_reason,
        players_matched=report.players_matched,
        players_failed=report.players_failed,
        unmatched_trade_count=report.unmatched_trade_count,
        unmatched_ledger_count=report.unmatched_ledger_count,
    )


@router.post("/trade-records/upload", response_model=TradeRecordUploadReport)
async def upload_trade_record(
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dependency),
):
    filename = (file.filename or "upload.xlsx").strip()
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "File must be an .xlsx workbook")

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File exceeds 20MB limit")
    if not raw:
        raise HTTPException(400, "Empty file")

    try:
        parsed = parse_trade_record_workbook(raw)
    except TradeRecordValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TradeRecordParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    slug = parsed.club_slug
    parsed_date = parsed.audit_date
    club_id = resolve_club_id(db, slug)
    club_name = CLUB_SLUG_TO_NAME[slug]
    timezone_policy = audit_timezone_for_slug(slug)

    existing = (
        db.query(TradeRecordUpload)
        .filter_by(club_slug=slug, audit_date=parsed_date)
        .first()
    )
    if existing is None:
        existing = (
            db.query(TradeRecordUpload)
            .filter_by(club_id=club_id, audit_date=parsed_date)
            .filter(
                (TradeRecordUpload.club_slug.is_(None))
                | (TradeRecordUpload.club_slug == slug)
            )
            .first()
        )
    replaced_previous = existing is not None
    metadata_json = json.dumps(
        {
            "club_text": parsed.metadata.club_text,
            "club_id_text": parsed.metadata.club_id_text,
            "date_text": parsed.metadata.date_text,
        }
    )

    if existing:
        db.query(TradeRecordLine).filter_by(upload_id=existing.id).delete(
            synchronize_session=False
        )
        existing.filename = filename
        existing.metadata_json = metadata_json
        existing.club_slug = slug
        existing.audit_timezone_policy = timezone_policy.value
        upload = existing
        db.flush()
    else:
        upload = TradeRecordUpload(
            club_id=club_id,
            club_slug=slug,
            audit_timezone_policy=timezone_policy.value,
            audit_date=parsed_date,
            filename=filename,
            metadata_json=metadata_json,
        )
        db.add(upload)
        db.flush()

    for tx in parsed.transactions:
        db.add(
            TradeRecordLine(
                upload_id=upload.id,
                sheet_row=tx.sheet_row,
                occurred_at=tx.occurred_at,
                amount=tx.amount,
                member_gg_player_id=tx.member_gg_player_id,
                member_nickname=tx.member_nickname,
                agent_gg_player_id=tx.agent_gg_player_id,
                super_agent_gg_player_id=tx.super_agent_gg_player_id,
            )
        )

    sync_report = sync_identities(
        db,
        club_id=club_id,
        club_slug=slug,
        identities=parsed.identities,
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Upload conflict for this club and date") from exc

    db.refresh(upload)

    return TradeRecordUploadReport(
        upload_id=upload.id,
        club_slug=slug,
        club_name=club_name,
        audit_date=parsed_date,
        audit_timezone_policy=timezone_policy.value,
        audit_timezone_label=audit_timezone_label(timezone_policy),
        filename=filename,
        replaced_previous=replaced_previous,
        transaction_rows_parsed=len(parsed.transactions),
        identities_extracted=sync_report.identities_extracted,
        postgres_inserted=sync_report.postgres_inserted,
        postgres_updated=sync_report.postgres_updated,
        gg_computer_upserted=sync_report.gg_computer_upserted,
        gg_computer_modified=sync_report.gg_computer_modified,
        gg_computer_skipped=sync_report.gg_computer_skipped,
        gg_computer_error=sync_report.gg_computer_error,
        skipped_rows=parsed.skipped_rows,
        warnings=parsed.warnings,
    )


@router.get("/trade-records", response_model=list[TradeRecordUploadSummary])
def list_trade_record_uploads(
    club_slug: str | None = Query(None),
    audit_date: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(
        TradeRecordUpload,
        func.count(TradeRecordLine.id).label("line_count"),
    ).outerjoin(TradeRecordLine, TradeRecordLine.upload_id == TradeRecordUpload.id)

    if club_slug:
        slug = club_slug.strip().lower()
        if slug not in CLUB_SLUG_TO_NAME:
            raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
        q = q.filter(TradeRecordUpload.club_slug == slug)

    if audit_date:
        q = q.filter(TradeRecordUpload.audit_date == _parse_audit_date(audit_date))

    rows = (
        q.group_by(TradeRecordUpload.id)
        .order_by(TradeRecordUpload.created_at.desc())
        .limit(limit)
        .all()
    )

    out: list[TradeRecordUploadSummary] = []
    for upload, line_count in rows:
        slug = upload.club_slug or slug_for_club_id(db, upload.club_id) or ""
        policy_value = upload.audit_timezone_policy
        tz_label = None
        if policy_value:
            try:
                tz_label = audit_timezone_label(AuditTimezonePolicy(policy_value))
            except ValueError:
                tz_label = None
        out.append(
            TradeRecordUploadSummary(
                id=upload.id,
                club_slug=slug,
                club_name=CLUB_SLUG_TO_NAME.get(slug, slug),
                audit_date=upload.audit_date,
                audit_timezone_policy=policy_value,
                audit_timezone_label=tz_label,
                filename=upload.filename,
                transaction_count=int(line_count or 0),
                created_at=upload.created_at or datetime.utcnow(),
            )
        )
    return out


@router.post("/early-rakeback/sync", response_model=EarlyRakebackSyncReport)
def sync_early_rakeback(
    audit_date: str = Query(..., description="Local audit calendar day (YYYY-MM-DD)"),
    club_slug: str | None = Query(None, description="Optional single club slug"),
    db: Session = Depends(get_db_dependency),
):
    parsed_date = _parse_audit_date(audit_date)
    club_slugs: list[str] | None = None
    if club_slug:
        slug = club_slug.strip().lower()
        if slug not in CLUB_SLUG_TO_NAME:
            raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
        club_slugs = [slug]

    try:
        report = sync_early_rakeback_for_date(
            db, parsed_date, club_slugs=club_slugs
        )
    except AonBetaConfigError as exc:
        raise HTTPException(503, str(exc)) from exc

    if report.clubs_synced == 0 and report.clubs_failed > 0:
        first_error = next(
            (c.error for c in report.clubs if c.error),
            "Early rakeback sync failed for all clubs",
        )
        if isinstance(first_error, str) and "AON_BETA" in first_error:
            raise HTTPException(503, first_error)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Sync conflict for this club and date") from exc

    return EarlyRakebackSyncReport(
        audit_date=report.audit_date,
        clubs_synced=report.clubs_synced,
        clubs_failed=report.clubs_failed,
        total_lines_fetched=report.total_lines_fetched,
        total_lines_stored=report.total_lines_stored,
        total_lines_skipped_unmapped=report.total_lines_skipped_unmapped,
        clubs=[
            {
                "club_slug": c.club_slug,
                "club_name": c.club_name,
                "snapshot_id": c.snapshot_id,
                "lines_fetched": c.lines_fetched,
                "lines_stored": c.lines_stored,
                "lines_skipped_unmapped": c.lines_skipped_unmapped,
                "skipped_nicknames": c.skipped_nicknames,
                "error": c.error,
            }
            for c in report.clubs
        ],
        warnings=report.warnings,
    )


@router.get("/early-rakeback/snapshots", response_model=list[EarlyRakebackSnapshotSummary])
def list_early_rakeback_snapshots(
    club_slug: str | None = Query(None),
    audit_date: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(EarlyRakebackSnapshot)

    if club_slug:
        slug = club_slug.strip().lower()
        if slug not in CLUB_SLUG_TO_NAME:
            raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
        q = q.filter(EarlyRakebackSnapshot.club_slug == slug)

    if audit_date:
        q = q.filter(EarlyRakebackSnapshot.audit_date == _parse_audit_date(audit_date))

    rows = (
        q.order_by(EarlyRakebackSnapshot.synced_at.desc())
        .limit(limit)
        .all()
    )

    out: list[EarlyRakebackSnapshotSummary] = []
    for snap in rows:
        slug = snap.club_slug or slug_for_club_id(db, snap.club_id) or ""
        out.append(
            EarlyRakebackSnapshotSummary(
                id=snap.id,
                club_slug=slug,
                club_name=CLUB_SLUG_TO_NAME.get(slug, slug),
                audit_date=snap.audit_date,
                lines_fetched=int(snap.lines_fetched or 0),
                lines_stored=int(snap.lines_stored or 0),
                lines_skipped_unmapped=int(snap.lines_skipped_unmapped or 0),
                synced_at=snap.synced_at or datetime.utcnow(),
            )
        )
    return out


@router.post("/reconcile", response_model=AuditReconcileReportSchema)
def reconcile_audit(
    club_slug: str = Query(..., description="Club slug (required)"),
    audit_date: str = Query(..., description="Local audit calendar day (YYYY-MM-DD)"),
    db: Session = Depends(get_db_dependency),
):
    slug = club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
    parsed_date = _parse_audit_date(audit_date)

    report = run_audit_reconcile(db, club_slug=slug, audit_date=parsed_date)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Reconcile conflict for this club and date") from exc

    return _report_to_schema(report)


@router.get("/reconcile/report", response_model=AuditReconcileReportSchema)
def get_reconcile_report(
    club_slug: str = Query(..., description="Club slug (required)"),
    audit_date: str = Query(..., description="Local audit calendar day (YYYY-MM-DD)"),
    db: Session = Depends(get_db_dependency),
):
    slug = club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
    parsed_date = _parse_audit_date(audit_date)

    report = load_stored_reconcile_report(
        db, club_slug=slug, audit_date=parsed_date
    )
    if report is None:
        raise HTTPException(
            404, "No reconcile run stored for this club and audit date"
        )
    return _report_to_schema(report)


@router.get("/reconcile/runs", response_model=list[AuditReconcileRunSummary])
def list_reconcile_runs(
    club_slug: str | None = Query(None),
    audit_date: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(AuditReconcileRun)

    if club_slug:
        slug = club_slug.strip().lower()
        if slug not in CLUB_SLUG_TO_NAME:
            raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
        q = q.filter(AuditReconcileRun.club_slug == slug)

    if audit_date:
        q = q.filter(AuditReconcileRun.audit_date == _parse_audit_date(audit_date))

    rows = (
        q.order_by(AuditReconcileRun.created_at.desc())
        .limit(limit)
        .all()
    )

    out: list[AuditReconcileRunSummary] = []
    for run in rows:
        slug = run.club_slug or slug_for_club_id(db, run.club_id) or ""
        out.append(
            AuditReconcileRunSummary(
                id=run.id,
                club_slug=slug,
                club_name=CLUB_SLUG_TO_NAME.get(slug, slug),
                audit_date=run.audit_date,
                status=run.status,
                players_matched=int(run.players_matched or 0),
                players_failed=int(run.players_failed or 0),
                unmatched_trade_count=int(run.unmatched_trade_count or 0),
                unmatched_ledger_count=int(run.unmatched_ledger_count or 0),
                created_at=run.created_at or datetime.utcnow(),
            )
        )
    return out


@router.get("/reconcile/export")
def export_reconcile(
    club_slug: str = Query(..., description="Club slug (required)"),
    audit_date: str = Query(..., description="Local audit calendar day (YYYY-MM-DD)"),
    db: Session = Depends(get_db_dependency),
):
    slug = club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
    parsed_date = _parse_audit_date(audit_date)

    report = run_audit_reconcile(
        db, club_slug=slug, audit_date=parsed_date, persist=False
    )
    content = build_reconcile_workbook_from_report(report)
    filename = f"reconcile-{slug}-{parsed_date.isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
