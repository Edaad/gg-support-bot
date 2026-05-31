"""Venmo payment ingest for Zapier (replaces Telegram step in Confirm Venmo Zaps)."""

import logging
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from bot.services.venmo_payments import (
    WEBHOOK_SECRET_ENV,
    ingest_venmo_payment,
)
from notification.constants import debug_notification_enabled

router = APIRouter(prefix="/api/venmo", tags=["venmo"])
logger = logging.getLogger(__name__)

LOOKUP_HEADER = "x-venmo-webhook-secret"


def _verify_webhook_secret(x_venmo_webhook_secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        if debug_notification_enabled():
            logger.error(
                "venmo ingest: auth rejected — %s not configured on server",
                WEBHOOK_SECRET_ENV,
            )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not x_venmo_webhook_secret or x_venmo_webhook_secret.strip() != expected:
        if debug_notification_enabled():
            logger.warning(
                "venmo ingest: auth rejected — invalid or missing %s header",
                LOOKUP_HEADER,
            )
        raise HTTPException(401, "Invalid webhook secret")


class VenmoPaymentIngestBody(BaseModel):
    payer_name: str = Field(..., min_length=1)
    amount: str | float | int
    venmo_handle: str = Field(..., min_length=1)
    goods_or_services: bool = False
    paid_at: str | None = None
    source_external_id: str | None = None
    test: bool = False


class VenmoPaymentIngestResponse(BaseModel):
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


@router.post("/payments", response_model=VenmoPaymentIngestResponse)
async def ingest_payment(
    body: VenmoPaymentIngestBody,
    x_venmo_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Ingest a Venmo payment from Zapier; notify staff Telegram group."""
    if debug_notification_enabled():
        logger.info(
            "venmo ingest: request received payer=%r amount=%r handle=%r "
            "goods_or_services=%s paid_at=%r test=%s source_external_id=%r",
            body.payer_name,
            body.amount,
            body.venmo_handle,
            body.goods_or_services,
            body.paid_at,
            body.test,
            body.source_external_id,
        )

    _verify_webhook_secret(x_venmo_webhook_secret)

    if debug_notification_enabled():
        logger.info("venmo ingest: auth ok")

    try:
        result = await ingest_venmo_payment(
            payer_name=body.payer_name,
            amount=body.amount,
            venmo_handle=body.venmo_handle,
            goods_or_services=body.goods_or_services,
            paid_at=body.paid_at,
            source_external_id=body.source_external_id,
            test=body.test,
        )
    except ValueError as e:
        if debug_notification_enabled():
            logger.warning("venmo ingest: rejected bad request — %s", e)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        if debug_notification_enabled():
            logger.error("venmo ingest: failed — %s", e)
        raise HTTPException(503, str(e)) from e

    if debug_notification_enabled():
        logger.info(
            "venmo ingest: ok payment_id=%s status=%s auto_bound=%s created=%s",
            result.payment_id,
            result.status,
            result.auto_bound,
            result.created,
        )

    return VenmoPaymentIngestResponse(
        payment_id=result.payment_id,
        status=result.status,
        auto_bound=result.auto_bound,
        created=result.created,
    )
