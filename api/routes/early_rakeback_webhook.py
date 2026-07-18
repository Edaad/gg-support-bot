"""Webhook from aon-beta to trigger early-rakeback sync into Postgres."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.aon_beta_client import AonBetaConfigError, aon_beta_api_key
from api.club_slug import CLUB_SLUG_TO_NAME
from api.early_rakeback_sync import (
    sync_report_to_dict,
    trigger_early_rakeback_sync_for_occurred_at,
)
from api.schemas_audit import EarlyRakebackSyncReport
from db.connection import get_db_dependency

router = APIRouter(prefix="/api/audit/early-rakeback", tags=["audit"])
logger = logging.getLogger(__name__)

INTERNAL_API_KEY_HEADER = "x-internal-api-key"


def _verify_internal_api_key(x_internal_api_key: str | None) -> None:
    expected = (aon_beta_api_key() or "").strip()
    if not expected:
        raise HTTPException(
            503,
            "AON_BETA_INTERNAL_API_KEY is not configured on the server",
        )
    if not x_internal_api_key or x_internal_api_key.strip() != expected:
        raise HTTPException(401, "Invalid internal API key")


class EarlyRakebackWebhookBody(BaseModel):
    club_slug: str = Field(..., min_length=1)
    occurred_at: datetime | None = Field(
        None,
        description="UTC ISO timestamp of the early-RB record (defaults to now)",
    )


def _parse_occurred_at(raw: datetime | None) -> datetime | None:
    if raw is None:
        return None
    if raw.tzinfo is None:
        return raw.replace(tzinfo=timezone.utc)
    return raw.astimezone(timezone.utc)


@router.post("/webhook", response_model=EarlyRakebackSyncReport)
def early_rakeback_webhook(
    body: EarlyRakebackWebhookBody,
    db: Session = Depends(get_db_dependency),
    x_internal_api_key: str | None = Header(None, alias=INTERNAL_API_KEY_HEADER),
):
    """Called by aon-beta when early rakeback entries change."""
    _verify_internal_api_key(x_internal_api_key)

    slug = body.club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise HTTPException(400, f"Unknown club slug: {body.club_slug!r}")

    occurred_at = _parse_occurred_at(body.occurred_at)

    try:
        report = trigger_early_rakeback_sync_for_occurred_at(
            db, slug, occurred_at=occurred_at
        )
    except AonBetaConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if report.clubs_synced == 0 and report.clubs_failed > 0:
        first_error = next(
            (c.error for c in report.clubs if c.error),
            "Early rakeback sync failed",
        )
        if isinstance(first_error, str) and "AON_BETA" in first_error:
            raise HTTPException(503, first_error)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Sync conflict for this club and date") from exc

    club_result = report.clubs[0] if report.clubs else None
    logger.info(
        "earlyrb webhook: synced club=%s audit_date=%s stored=%s skipped=%s",
        slug,
        report.audit_date,
        report.total_lines_stored,
        report.total_lines_skipped_unmapped,
    )
    if club_result and club_result.error:
        logger.warning(
            "earlyrb webhook: club=%s audit_date=%s error=%s",
            slug,
            report.audit_date,
            club_result.error,
        )

    return EarlyRakebackSyncReport(**sync_report_to_dict(report))
