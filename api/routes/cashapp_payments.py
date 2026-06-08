"""Cash App payment ingest for Zapier."""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from bot.services.cashapp_payments import (
    WEBHOOK_SECRET_ENV,
    ingest_cashapp_payment,
)
from notification.constants import debug_notification_enabled

router = APIRouter(prefix="/api/cashapp", tags=["cashapp"])
logger = logging.getLogger(__name__)

LOOKUP_HEADER = "x-cashapp-webhook-secret"


def _nested_data_dict(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _verify_webhook_secret(x_cashapp_webhook_secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        if debug_notification_enabled():
            logger.error(
                "cashapp ingest: auth rejected — %s not configured on server",
                WEBHOOK_SECRET_ENV,
            )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not x_cashapp_webhook_secret or x_cashapp_webhook_secret.strip() != expected:
        if debug_notification_enabled():
            logger.warning(
                "cashapp ingest: auth rejected — invalid or missing %s header",
                LOOKUP_HEADER,
            )
        raise HTTPException(401, "Invalid webhook secret")


class CashAppPaymentIngestBody(BaseModel):
    payer_name: str = Field(..., min_length=1)
    amount: str | float | int
    cashapp_handle: str = Field(..., min_length=1)
    paid_at: str | None = None
    source_external_id: str | None = None
    memo: str | None = None
    test: bool = False


class CashAppPaymentIngestResponse(BaseModel):
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


@router.post("/payments", response_model=CashAppPaymentIngestResponse)
async def ingest_payment(
    request: Request,
    x_cashapp_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Ingest a Cash App payment from Zapier; notify staff Telegram group."""
    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("cashapp ingest: invalid JSON — %s", exc)
        raise HTTPException(400, "Invalid JSON body") from exc

    if not isinstance(payload, dict):
        logger.warning(
            "cashapp ingest: JSON body must be an object, got %s",
            type(payload).__name__,
        )
        raise HTTPException(400, "JSON body must be an object")

    nested = _nested_data_dict(payload)
    logger.info(
        "cashapp ingest: request received top_keys=%s memo_root=%r memo_in_data=%r "
        "test_root=%s test_in_data=%s source_external_id_root=%r "
        "source_external_id_in_data=%r payer_root=%r payer_in_data=%r",
        sorted(payload.keys()),
        payload.get("memo"),
        nested.get("memo"),
        payload.get("test"),
        nested.get("test"),
        payload.get("source_external_id"),
        nested.get("source_external_id"),
        payload.get("payer_name"),
        nested.get("payer_name"),
    )

    try:
        body = CashAppPaymentIngestBody.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "cashapp ingest: validation failed top_keys=%s errors=%s",
            sorted(payload.keys()),
            exc.errors(),
        )
        raise HTTPException(422, detail=exc.errors()) from exc

    logger.info(
        "cashapp ingest: parsed body payer=%r amount=%r handle=%r paid_at=%r "
        "memo=%r test=%s source_external_id=%r",
        body.payer_name,
        body.amount,
        body.cashapp_handle,
        body.paid_at,
        body.memo,
        body.test,
        body.source_external_id,
    )

    _verify_webhook_secret(x_cashapp_webhook_secret)

    try:
        result = await ingest_cashapp_payment(
            payer_name=body.payer_name,
            amount=body.amount,
            cashapp_handle=body.cashapp_handle,
            paid_at=body.paid_at,
            source_external_id=body.source_external_id,
            memo=body.memo,
            test=body.test,
        )
    except ValueError as e:
        logger.warning("cashapp ingest: rejected bad request — %s", e)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        logger.error("cashapp ingest: failed — %s", e)
        raise HTTPException(503, str(e)) from e

    logger.info(
        "cashapp ingest: completed payment_id=%s status=%s auto_bound=%s created=%s",
        result.payment_id,
        result.status,
        result.auto_bound,
        result.created,
    )

    return CashAppPaymentIngestResponse(
        payment_id=result.payment_id,
        status=result.status,
        auto_bound=result.auto_bound,
        created=result.created,
    )
