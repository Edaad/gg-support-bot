"""Crypto payment ingest for Zapier (Arkham alerts)."""

import logging
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from bot.services.crypto_payments import (
    WEBHOOK_SECRET_ENV,
    ingest_crypto_payment,
)
from notification.constants import debug_notification_enabled

router = APIRouter(prefix="/api/crypto", tags=["crypto"])
logger = logging.getLogger(__name__)

LOOKUP_HEADER = "x-crypto-webhook-secret"


def _verify_webhook_secret(x_crypto_webhook_secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        if debug_notification_enabled():
            logger.error(
                "crypto ingest: auth rejected — %s not configured on server",
                WEBHOOK_SECRET_ENV,
            )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not x_crypto_webhook_secret or x_crypto_webhook_secret.strip() != expected:
        if debug_notification_enabled():
            logger.warning(
                "crypto ingest: auth rejected — invalid or missing %s header",
                LOOKUP_HEADER,
            )
        raise HTTPException(401, "Invalid webhook secret")


class CryptoPaymentIngestBody(BaseModel):
    amount: str | float | int
    token_symbol: str = Field(..., min_length=1)
    chain: str = Field(..., min_length=1)
    from_address: str = Field(..., min_length=1)
    to_address: str = Field(..., min_length=1)
    transaction_hash: str = Field(..., min_length=1)
    token_name: str | None = None
    from_entity_name: str | None = None
    paid_at: str | None = None
    source_external_id: str | None = None
    alert_name: str | None = Field(..., min_length=1)
    test: bool = False


class CryptoPaymentIngestResponse(BaseModel):
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


@router.post("/payments", response_model=CryptoPaymentIngestResponse)
async def ingest_payment(
    body: CryptoPaymentIngestBody,
    x_crypto_webhook_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Ingest a crypto payment from Zapier; notify staff Telegram group."""
    if debug_notification_enabled():
        logger.info(
            "crypto ingest: request received amount=%r token=%r chain=%r "
            "from=%r to=%r tx=%r paid_at=%r alert=%r test=%s source_external_id=%r",
            body.amount,
            body.token_symbol,
            body.chain,
            body.from_address,
            body.to_address,
            body.transaction_hash,
            body.paid_at,
            body.alert_name,
            body.test,
            body.source_external_id,
        )

    _verify_webhook_secret(x_crypto_webhook_secret)

    try:
        result = await ingest_crypto_payment(
            amount=body.amount,
            token_symbol=body.token_symbol,
            chain=body.chain,
            from_address=body.from_address,
            to_address=body.to_address,
            transaction_hash=body.transaction_hash,
            token_name=body.token_name,
            from_entity_name=body.from_entity_name,
            paid_at=body.paid_at,
            source_external_id=body.source_external_id,
            alert_name=body.alert_name,
            test=body.test,
        )
    except ValueError as e:
        if debug_notification_enabled():
            logger.warning("crypto ingest: rejected bad request — %s", e)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        if debug_notification_enabled():
            logger.error("crypto ingest: failed — %s", e)
        raise HTTPException(503, str(e)) from e

    return CryptoPaymentIngestResponse(
        payment_id=result.payment_id,
        status=result.status,
        auto_bound=result.auto_bound,
        created=result.created,
    )
