"""Outbound send ingest (Zapier) and dashboard list."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth import get_current_admin
from api.method_owner import MethodOwnerSlug
from api.outbound_sends import (
    ingest_outbound_send,
    list_outbound_sends,
    parse_positive_amount_cents,
)
from api.webhook_ingest_audit import (
    enrich_outbound_ingest_success,
    set_webhook_ingest_error,
)
from db.connection import get_db
from db.models import OutboundSend

router = APIRouter(prefix="/api/outbound-sends", tags=["outbound-sends"])

WEBHOOK_SECRET_ENV = "OUTBOUND_SEND_WEBHOOK_SECRET"
LOOKUP_HEADER = "x-outbound-webhook-secret"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class OutboundSendIngestBody(BaseModel):
    method: str = Field(..., min_length=1)
    tag: str = Field(..., min_length=1)
    method_owner: MethodOwnerSlug
    recipient: str = Field(..., min_length=1)
    amount: str | float | int
    source_external_id: str = Field(..., min_length=1)
    paid_at: str | None = None


class OutboundSendIngestResponse(BaseModel):
    id: int
    created: bool
    tag_matched: bool
    warning: str | None


class OutboundSendRead(BaseModel):
    id: int
    method: str
    tag: str
    method_owner: str
    recipient: str
    amount_cents: int
    tag_matched: bool
    source_external_id: str
    paid_at: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class OutboundSendListResponse(BaseModel):
    items: list[OutboundSendRead]
    total: int
    limit: int
    offset: int


def _verify_webhook_secret(secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        raise HTTPException(
            503, f"{WEBHOOK_SECRET_ENV} is not configured on the server"
        )
    if not secret or secret.strip() != expected:
        raise HTTPException(401, "Invalid webhook secret")


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(400, f"Invalid datetime: {value}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_read(row: OutboundSend) -> OutboundSendRead:
    return OutboundSendRead.model_validate(row)


@router.post("", response_model=OutboundSendIngestResponse)
def ingest_send(
    request: Request,
    body: OutboundSendIngestBody,
    x_outbound_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    _verify_webhook_secret(x_outbound_webhook_secret)
    try:
        with get_db() as db:
            result = ingest_outbound_send(
                db,
                method=body.method,
                tag=body.tag,
                method_owner=body.method_owner,
                recipient=body.recipient,
                amount=body.amount,
                source_external_id=body.source_external_id,
                paid_at=body.paid_at,
            )
    except ValueError as e:
        set_webhook_ingest_error(request, str(e))
        raise HTTPException(400, str(e)) from e
    amount_cents = None
    try:
        amount_cents = parse_positive_amount_cents(body.amount)
    except ValueError:
        amount_cents = None
    enrich_outbound_ingest_success(
        request,
        source_external_id=body.source_external_id,
        payment_id=result.id,
        method_owner=body.method_owner,
        recipient=body.recipient,
        amount_cents=amount_cents,
        created=result.created,
        tag_matched=result.tag_matched,
        warning=result.warning,
    )
    return OutboundSendIngestResponse(
        id=result.id,
        created=result.created,
        tag_matched=result.tag_matched,
        warning=result.warning,
    )


@router.get("", response_model=OutboundSendListResponse)
def list_sends(
    method: str | None = Query(None),
    tag: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    _role: str = Depends(get_current_admin),
):
    limit = max(1, min(limit, _MAX_LIMIT))
    offset = max(0, offset)
    parsed_from = _parse_dt(from_dt)
    parsed_to = _parse_dt(to_dt)
    try:
        with get_db() as db:
            items, total = list_outbound_sends(
                db,
                method=method,
                tag=tag,
                from_dt=parsed_from,
                to_dt=parsed_to,
                limit=limit,
                offset=offset,
            )
            payload = [_to_read(row) for row in items]
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return OutboundSendListResponse(
        items=payload,
        total=total,
        limit=limit,
        offset=offset,
    )
