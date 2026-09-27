"""Plaid webhooks. No dashboard JWT; the Plaid-Verification header is the auth."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from api.plaid_bank import sync_for_webhook
from api.plaid_webhook import PlaidWebhookError, verify_plaid_webhook

router = APIRouter(prefix="/api/plaid", tags=["plaid-webhook"])
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def plaid_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signed = request.headers.get("plaid-verification") or ""
    try:
        verify_plaid_webhook(body, signed)
    except PlaidWebhookError as exc:
        raise HTTPException(401, "Invalid webhook") from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "Invalid webhook body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid webhook body")
    kind = payload.get("webhook_type")
    code = payload.get("webhook_code")
    item_id = payload.get("item_id")
    logger.info(
        "plaid webhook type=%s code=%s item_id=%s",
        kind,
        code,
        item_id,
    )
    if kind == "TRANSACTIONS" and code == "SYNC_UPDATES_AVAILABLE" and item_id:
        background_tasks.add_task(
            sync_for_webhook, str(item_id), datetime.now(timezone.utc)
        )
    return {"ok": True}
