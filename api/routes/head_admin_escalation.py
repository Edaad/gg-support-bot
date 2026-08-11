"""Inbound webhook: post arbitrary text to the head-admin Slack escalation channel.

For Make/Zapier (e.g. Hub cashout wait-email → Slack). Authenticated with a
shared secret header; does not require dashboard JWT or club escalation toggle.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/head-admin-escalation", tags=["head-admin-escalation"])
logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "HEAD_ADMIN_ESCALATION_WEBHOOK_SECRET"
LOOKUP_HEADER = "x-head-admin-escalation-webhook-secret"
SOURCE = "head_admin_escalation_webhook"


def _verify_webhook_secret(secret: str | None) -> None:
    expected = (os.getenv(WEBHOOK_SECRET_ENV) or "").strip()
    if not expected:
        logger.error(
            "head_admin_escalation webhook: auth rejected — %s not configured",
            WEBHOOK_SECRET_ENV,
        )
        raise HTTPException(
            503,
            f"{WEBHOOK_SECRET_ENV} is not configured on the server",
        )
    if not secret or secret.strip() != expected:
        logger.warning(
            "head_admin_escalation webhook: auth rejected — invalid or missing %s",
            LOOKUP_HEADER,
        )
        raise HTTPException(401, "Invalid webhook secret")


class HeadAdminEscalationBody(BaseModel):
    message: str = Field(..., min_length=1)


class HeadAdminEscalationResponse(BaseModel):
    ok: bool


@router.post("", response_model=HeadAdminEscalationResponse)
@router.post("/", response_model=HeadAdminEscalationResponse, include_in_schema=False)
async def post_head_admin_escalation(
    body: HeadAdminEscalationBody,
    x_head_admin_escalation_webhook_secret: str | None = Header(
        None, alias=LOOKUP_HEADER
    ),
):
    """Post ``message`` verbatim to ``SLACK_HEAD_ADMIN_ESCALATION_CHANNEL_ID``."""
    _verify_webhook_secret(x_head_admin_escalation_webhook_secret)

    text = (body.message or "").strip()
    if not text:
        raise HTTPException(422, "message must be non-empty")

    from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

    ok = await notify_slack_head_admin_escalation(text, source=SOURCE)
    if not ok:
        logger.warning("head_admin_escalation webhook: slack post failed")
        raise HTTPException(502, "Failed to post to head-admin Slack channel")

    logger.info("head_admin_escalation webhook: posted len=%s", len(text))
    return HeadAdminEscalationResponse(ok=True)
