"""Zelle payment ingest for Zapier."""

import logging
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from bot.services.zelle_payments import (
    WEBHOOK_SECRET_ENV,
    ingest_zelle_payment,
)
from notification.constants import debug_notification_enabled

router = APIRouter(prefix="/api/zelle", tags=["zelle"])
logger = logging.getLogger(__name__)

LOOKUP_HEADER = "x-zelle-webhook-secret"


def _verify_webhook_secret(x_zelle_webhook_secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        if debug_notification_enabled():
            logger.error(
                "zelle ingest: auth rejected — %s not configured on server",
                WEBHOOK_SECRET_ENV,
            )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not x_zelle_webhook_secret or x_zelle_webhook_secret.strip() != expected:
        if debug_notification_enabled():
            logger.warning(
                "zelle ingest: auth rejected — invalid or missing %s header",
                LOOKUP_HEADER,
            )
        raise HTTPException(401, "Invalid webhook secret")


class ZellePaymentIngestBody(BaseModel):
    payer_name: str = Field(..., min_length=1)
    amount: str | float | int
    zelle_recipient: str = Field(..., min_length=1)
    paid_at: str | None = None
    source_external_id: str | None = None
    memo: str | None = None
    test: bool = False


class ZellePaymentIngestResponse(BaseModel):
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


@router.post("/payments", response_model=ZellePaymentIngestResponse)
async def ingest_payment(
    body: ZellePaymentIngestBody,
    x_zelle_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Ingest a Zelle payment from Zapier; notify staff Telegram group."""
    if debug_notification_enabled():
        logger.info(
            "zelle ingest: request received payer=%r amount=%r recipient=%r "
            "paid_at=%r memo=%r test=%s source_external_id=%r",
            body.payer_name,
            body.amount,
            body.zelle_recipient,
            body.paid_at,
            body.memo,
            body.test,
            body.source_external_id,
        )

    _verify_webhook_secret(x_zelle_webhook_secret)

    try:
        result = await ingest_zelle_payment(
            payer_name=body.payer_name,
            amount=body.amount,
            zelle_recipient=body.zelle_recipient,
            paid_at=body.paid_at,
            source_external_id=body.source_external_id,
            memo=body.memo,
            test=body.test,
        )
    except ValueError as e:
        if debug_notification_enabled():
            logger.warning("zelle ingest: rejected bad request — %s", e)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        if debug_notification_enabled():
            logger.error("zelle ingest: failed — %s", e)
        raise HTTPException(503, str(e)) from e

    return ZellePaymentIngestResponse(
        payment_id=result.payment_id,
        status=result.status,
        auto_bound=result.auto_bound,
        created=result.created,
    )
