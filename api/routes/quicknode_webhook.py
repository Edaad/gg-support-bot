"""QuickNode Webhooks receiver. No dashboard JWT; the x-qn-signature HMAC is the auth.

The payload is an untrusted hint: only (chain, tx hash) pairs are kept, and the
verifier job re-reads each tx from two sources before anything is credited.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from bot.services.quicknode.config import CHAINS, webhook_secrets
from bot.services.quicknode.hints import SignatureError, extract_hints, verify_signature
from bot.services.quicknode.store import record_hints

router = APIRouter(prefix="/api/crypto/quicknode", tags=["quicknode-webhook"])
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def quicknode_webhook(request: Request, chain: str = Query(...)):
    chain_norm = (chain or "").strip().lower()
    if chain_norm not in CHAINS:
        raise HTTPException(400, "Unknown chain")
    body = await request.body()
    try:
        verify_signature(
            body=body,
            nonce=request.headers.get("x-qn-nonce"),
            timestamp=request.headers.get("x-qn-timestamp"),
            signature=request.headers.get("x-qn-signature"),
            secrets=webhook_secrets(),
        )
    except SignatureError as exc:
        logger.warning("quicknode webhook rejected chain=%s: %s", chain_norm, exc)
        raise HTTPException(401, "Invalid signature") from exc
    try:
        payload = json.loads(body) if body else None
    except ValueError as exc:
        raise HTTPException(400, "Invalid body") from exc
    hashes = extract_hints(chain_norm, payload)
    inserted = await asyncio.to_thread(record_hints, chain_norm, hashes)
    logger.info(
        "quicknode webhook chain=%s hints=%s new=%s", chain_norm, len(hashes), inserted
    )
    return {"ok": True, "hints": len(hashes), "new": inserted}
