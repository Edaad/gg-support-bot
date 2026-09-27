"""Verify Plaid webhook JWTs. The signed body must match and be recent."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import jwt

from api.plaid_bank import PlaidError, plaid_post

_MAX_AGE_SECONDS = 300


class PlaidWebhookError(Exception):
    pass


def verify_plaid_webhook(body: bytes, signed_jwt: str) -> None:
    if not signed_jwt:
        raise PlaidWebhookError("missing signature")
    try:
        header = jwt.get_unverified_header(signed_jwt)
    except jwt.PyJWTError as exc:
        raise PlaidWebhookError("bad signature") from exc
    kid = header.get("kid")
    if not kid:
        raise PlaidWebhookError("missing kid")
    try:
        key_data = plaid_post("/webhook_verification_key/get", {"key_id": kid})
    except PlaidError as exc:
        raise PlaidWebhookError("key lookup failed") from exc
    jwk = key_data.get("key")
    if not isinstance(jwk, dict):
        raise PlaidWebhookError("key lookup failed")
    try:
        public_key = jwt.PyJWK.from_dict(jwk).key
        claims = jwt.decode(signed_jwt, public_key, algorithms=["ES256"])
    except jwt.PyJWTError as exc:
        raise PlaidWebhookError("bad signature") from exc
    body_hash = hashlib.sha256(body).hexdigest()
    if claims.get("request_body_sha256") != body_hash:
        raise PlaidWebhookError("body mismatch")
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)):
        raise PlaidWebhookError("missing iat")
    age = datetime.now(timezone.utc).timestamp() - float(iat)
    if age > _MAX_AGE_SECONDS or age < -60:
        raise PlaidWebhookError("stale signature")
