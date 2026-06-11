"""PayPal payment ingest for Zapier."""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from bot.services.paypal_payments import (
    WEBHOOK_SECRET_ENV,
    ingest_paypal_payment,
)
from notification.constants import debug_notification_enabled

router = APIRouter(prefix="/api/paypal", tags=["paypal"])
logger = logging.getLogger(__name__)

LOOKUP_HEADER = "x-paypal-webhook-secret"


def _verify_webhook_secret(x_paypal_webhook_secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        if debug_notification_enabled():
            logger.error(
                "paypal ingest: auth rejected — %s not configured on server",
                WEBHOOK_SECRET_ENV,
            )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not x_paypal_webhook_secret or x_paypal_webhook_secret.strip() != expected:
        if debug_notification_enabled():
            logger.warning(
                "paypal ingest: auth rejected — invalid or missing %s header",
                LOOKUP_HEADER,
            )
        raise HTTPException(401, "Invalid webhook secret")


class PayPalPaymentIngestBody(BaseModel):
    payer_name: str = Field(..., min_length=1)
    amount: str | float | int
    paypal_email: str = Field(..., min_length=1)
    paid_at: str | None = None
    source_external_id: str | None = None
    memo: str | None = None
    test: bool = False


class PayPalPaymentIngestResponse(BaseModel):
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


@router.post("/payments", response_model=PayPalPaymentIngestResponse)
async def ingest_payment(
    request: Request,
    x_paypal_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Ingest a PayPal payment from Zapier; notify staff Telegram group."""
    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("paypal ingest: invalid JSON — %s", exc)
        raise HTTPException(400, "Invalid JSON body") from exc

    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON body must be an object")

    try:
        body = PayPalPaymentIngestBody.model_validate(payload)
    except ValidationError as exc:
        logger.warning("paypal ingest: validation failed errors=%s", exc.errors())
        raise HTTPException(422, detail=exc.errors()) from exc

    logger.info(
        "paypal ingest: parsed body payer=%r amount=%r email=%r paid_at=%r "
        "memo=%r test=%s source_external_id=%r",
        body.payer_name,
        body.amount,
        body.paypal_email,
        body.paid_at,
        body.memo,
        body.test,
        body.source_external_id,
    )

    _verify_webhook_secret(x_paypal_webhook_secret)

    try:
        result = await ingest_paypal_payment(
            payer_name=body.payer_name,
            amount=body.amount,
            paypal_email=body.paypal_email,
            paid_at=body.paid_at,
            source_external_id=body.source_external_id,
            memo=body.memo,
            test=body.test,
        )
    except ValueError as e:
        logger.warning("paypal ingest: rejected bad request — %s", e)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        logger.error("paypal ingest: failed — %s", e)
        raise HTTPException(503, str(e)) from e

    logger.info(
        "paypal ingest: completed payment_id=%s status=%s auto_bound=%s created=%s",
        result.payment_id,
        result.status,
        result.auto_bound,
        result.created,
    )

    return PayPalPaymentIngestResponse(
        payment_id=result.payment_id,
        status=result.status,
        auto_bound=result.auto_bound,
        created=result.created,
    )
