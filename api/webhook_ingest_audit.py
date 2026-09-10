"""Persist webhook ingest request metadata for payment + Stripe endpoints."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from db.connection import get_db
from db.models import WebhookIngestRequest

logger = logging.getLogger(__name__)

WEBHOOK_INGEST_PATHS: dict[str, str] = {
    "/api/venmo/payments": "venmo",
    "/api/zelle/payments": "zelle",
    "/api/cashapp/payments": "cashapp",
    "/api/paypal/payments": "paypal",
    "/api/crypto/payments": "crypto",
    "/api/stripe/webhook": "stripe",
}

OUTCOME_AUTH_FAILED = "auth_failed"
OUTCOME_MISCONFIGURED = "misconfigured"
OUTCOME_VALIDATION_ERROR = "validation_error"
OUTCOME_REJECTED = "rejected"
OUTCOME_PROCESSING_ERROR = "processing_error"
OUTCOME_SUCCESS_CREATED = "success_created"
OUTCOME_SUCCESS_DUPLICATE = "success_duplicate"
OUTCOME_STRIPE_PROCESSED = "stripe_processed"
OUTCOME_STRIPE_IGNORED = "stripe_ignored"

_STRIPE_HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }
)


@dataclass
class WebhookIngestContext:
    source_external_id: str | None = None
    payment_id: int | None = None
    method_owner: str | None = None
    payer_summary: str | None = None
    amount_cents: int | None = None
    is_test: bool | None = None
    stripe_event_type: str | None = None
    stripe_checkout_session_id: str | None = None
    stripe_processed: bool | None = None
    error_message: str | None = None
    response_json: dict[str, Any] | None = None
    request_body: dict[str, Any] | list[Any] | None = None
    outcome_override: str | None = None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def source_for_path(path: str) -> str | None:
    return WEBHOOK_INGEST_PATHS.get(path)


def parse_request_body(
    body_bytes: bytes, content_type: str | None
) -> dict[str, Any] | list[Any] | None:
    if not body_bytes:
        return None
    ct = (content_type or "").lower()
    if "json" in ct or not content_type:
        try:
            parsed = json.loads(body_bytes)
            if isinstance(parsed, (dict, list)):
                return parsed
            return {"_value": parsed}
        except json.JSONDecodeError:
            return {
                "_raw": body_bytes.decode("utf-8", errors="replace")[:10000],
            }
    return {"_raw": body_bytes.decode("utf-8", errors="replace")[:10000]}


def _extract_error_detail(response: Response | None) -> str | None:
    if response is None:
        return None
    try:
        body = getattr(response, "body", None)
        if not body:
            return None
        payload = json.loads(body)
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return json.dumps(detail)
    except Exception:
        return None
    return None


def derive_outcome(
    *,
    source: str,
    http_status_code: int,
    ctx: WebhookIngestContext,
) -> str:
    if ctx.outcome_override:
        return ctx.outcome_override
    if http_status_code == 401:
        return OUTCOME_AUTH_FAILED
    if http_status_code == 422:
        return OUTCOME_VALIDATION_ERROR
    if http_status_code == 400:
        return OUTCOME_REJECTED
    if http_status_code == 503:
        msg = (ctx.error_message or "").lower()
        if "not configured" in msg:
            return OUTCOME_MISCONFIGURED
        return OUTCOME_PROCESSING_ERROR
    if http_status_code >= 500:
        return OUTCOME_PROCESSING_ERROR

    if source == "stripe" and http_status_code == 200:
        if ctx.stripe_processed is True:
            return OUTCOME_STRIPE_PROCESSED
        if ctx.stripe_processed is False:
            return OUTCOME_STRIPE_IGNORED
        if ctx.stripe_event_type in _STRIPE_HANDLED_EVENTS:
            return OUTCOME_STRIPE_PROCESSED
        return OUTCOME_STRIPE_IGNORED

    if http_status_code == 200:
        created = None
        if ctx.response_json is not None:
            created = ctx.response_json.get("created")
        if created is True:
            return OUTCOME_SUCCESS_CREATED
        if created is False:
            return OUTCOME_SUCCESS_DUPLICATE
        return OUTCOME_SUCCESS_CREATED

    return OUTCOME_PROCESSING_ERROR


def record_webhook_ingest_request(
    *,
    source: str,
    endpoint_path: str,
    http_status_code: int,
    outcome: str,
    duration_ms: int,
    ctx: WebhookIngestContext,
) -> int | None:
    try:
        with get_db() as session:
            row = WebhookIngestRequest(
                source=source,
                endpoint_path=endpoint_path,
                http_status_code=http_status_code,
                outcome=outcome,
                duration_ms=duration_ms,
                source_external_id=_truncate(ctx.source_external_id, 255),
                payment_id=ctx.payment_id,
                method_owner=_truncate(ctx.method_owner, 32),
                payer_summary=_truncate(ctx.payer_summary, 64),
                amount_cents=ctx.amount_cents,
                is_test=ctx.is_test,
                stripe_event_type=_truncate(ctx.stripe_event_type, 64),
                stripe_checkout_session_id=_truncate(
                    ctx.stripe_checkout_session_id, 255
                ),
                error_message=_truncate(ctx.error_message, 500),
                request_body=ctx.request_body,
                response_json=ctx.response_json,
            )
            session.add(row)
            session.flush()
            return int(row.id)
    except Exception:
        logger.exception("webhook_ingest_audit: failed to persist request")
        return None


def set_webhook_ingest_error(request: Request, message: str) -> None:
    ctx = getattr(request.state, "webhook_ingest", None)
    if ctx is None:
        return
    ctx.error_message = _truncate(message, 500)


def enrich_payment_ingest_success(
    request: Request,
    *,
    source_external_id: str | None,
    payment_id: int,
    method_owner: str,
    payer_summary: str,
    amount_cents: int | None,
    is_test: bool,
    status: str,
    auto_bound: bool,
    created: bool,
) -> None:
    ctx = getattr(request.state, "webhook_ingest", None)
    if ctx is None:
        return
    ctx.source_external_id = source_external_id
    ctx.payment_id = payment_id
    ctx.method_owner = method_owner
    ctx.payer_summary = payer_summary
    ctx.amount_cents = amount_cents
    ctx.is_test = is_test
    ctx.response_json = {
        "status": status,
        "auto_bound": auto_bound,
        "created": created,
    }


class WebhookIngestMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        source = source_for_path(path)
        if source is None:
            return await call_next(request)

        started = time.perf_counter()
        body_bytes = await request.body()

        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request = Request(request.scope, receive)
        ctx = WebhookIngestContext(
            request_body=parse_request_body(
                body_bytes, request.headers.get("content-type")
            ),
        )
        request.state.webhook_ingest = ctx

        response: Response | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            if not ctx.error_message:
                ctx.error_message = _truncate(_extract_error_detail(response), 500)
            outcome = derive_outcome(
                source=source,
                http_status_code=status_code,
                ctx=ctx,
            )
            recorded_id = record_webhook_ingest_request(
                source=source,
                endpoint_path=path,
                http_status_code=status_code,
                outcome=outcome,
                duration_ms=duration_ms,
                ctx=ctx,
            )
            try:
                from bot.services.payment_ingest_escalation import (
                    maybe_notify_payment_ingest_escalation,
                )

                await maybe_notify_payment_ingest_escalation(
                    source=source,
                    http_status_code=status_code,
                    ctx=ctx,
                    recorded_id=recorded_id,
                )
            except Exception:
                logger.warning(
                    "webhook_ingest_audit: escalation notify failed source=%s",
                    source,
                    exc_info=True,
                )
