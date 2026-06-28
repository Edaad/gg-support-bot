"""Audit trade record upload API."""

from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.club_slug import CLUB_SLUG_TO_NAME, resolve_club_id, slug_for_club_id
from api.schemas_audit import TradeRecordUploadReport, TradeRecordUploadSummary
from api.trade_record_parser import (
    TradeRecordParseError,
    TradeRecordValidationError,
    parse_trade_record_workbook,
    validate_metadata,
)
from api.trade_record_sync import sync_identities
from db.connection import get_db_dependency
from db.models import TradeRecordLine, TradeRecordUpload

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


@router.post("/trade-records/upload", response_model=TradeRecordUploadReport)
async def upload_trade_record(
    club_slug: str = Form(...),
    audit_date: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dependency),
):
    slug = club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")

    parsed_date = _parse_audit_date(audit_date)
    club_id = resolve_club_id(db, slug)
    club_name = CLUB_SLUG_TO_NAME[slug]

    filename = (file.filename or "upload.xlsx").strip()
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "File must be an .xlsx workbook")

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File exceeds 20MB limit")
    if not raw:
        raise HTTPException(400, "Empty file")

    try:
        parsed = parse_trade_record_workbook(raw, audit_date=parsed_date)
        validate_metadata(
            parsed.metadata,
            club_slug=slug,
            audit_date=parsed_date,
        )
    except TradeRecordValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TradeRecordParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = (
        db.query(TradeRecordUpload)
        .filter_by(club_id=club_id, audit_date=parsed_date)
        .first()
    )
    replaced_previous = existing is not None
    replaced_id = existing.id if existing else None

    if existing:
        db.query(TradeRecordLine).filter_by(upload_id=existing.id).delete(
            synchronize_session=False
        )
        db.delete(existing)
        db.flush()

    upload = TradeRecordUpload(
        club_id=club_id,
        audit_date=parsed_date,
        filename=filename,
        metadata_json=json.dumps(
            {
                "club_text": parsed.metadata.club_text,
                "date_text": parsed.metadata.date_text,
            }
        ),
        replaced_upload_id=replaced_id,
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
        club_id = resolve_club_id(db, slug)
        q = q.filter(TradeRecordUpload.club_id == club_id)

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
        slug = slug_for_club_id(db, upload.club_id) or ""
        out.append(
            TradeRecordUploadSummary(
                id=upload.id,
                club_slug=slug,
                club_name=CLUB_SLUG_TO_NAME.get(slug, slug),
                audit_date=upload.audit_date,
                filename=upload.filename,
                transaction_count=int(line_count or 0),
                created_at=upload.created_at or datetime.utcnow(),
            )
        )
    return out
