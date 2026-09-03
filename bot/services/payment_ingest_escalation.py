"""Head-admin Slack alerts for payment / Stripe webhook ingest failures."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, or_

from db.connection import get_db
from db.models import WebhookIngestRequest

logger = logging.getLogger(__name__)

ALERT_HTTP_ERROR = "http_error"
AlertKind = Literal["http_error"]

DEDUPE_WINDOW = timedelta(minutes=15)

SOURCE_HTTP_ERROR = "payment_ingest_http_error"

TITLE_HTTP_ERROR = "Payment ingest failure"


def should_alert_ingest(
    *,
    http_status_code: int,
    response_json: dict[str, Any] | None = None,
) -> AlertKind | None:
    """Return alert kind for HTTP failures only; duplicates (created=false) are ignored."""
    _ = response_json
    if http_status_code >= 400:
        return ALERT_HTTP_ERROR
    return None


def is_test_ingest(
    *,
    is_test: bool | None,
    request_body: dict[str, Any] | list[Any] | None,
) -> bool:
    if is_test is True:
        return True
    if isinstance(request_body, dict) and request_body.get("test") is True:
        return True
    return False


def ingest_dedupe_key(
    *,
    source_external_id: str | None,
    stripe_checkout_session_id: str | None,
    payment_id: int | None,
) -> str:
    external = (source_external_id or "").strip()
    if external:
        return f"ext:{external}"
    stripe_cs = (stripe_checkout_session_id or "").strip()
    if stripe_cs:
        return f"stripe:{stripe_cs}"
    if payment_id is not None:
        return f"pay:{int(payment_id)}"
    return "none"


def format_amount_line(amount_cents: int | None) -> str:
    if amount_cents is None:
        return "unknown"
    return f"${amount_cents / 100:.2f}"


def format_ingest_escalation_text(
    *,
    alert_kind: AlertKind,
    source: str,
    amount_cents: int | None,
    error_message: str | None,
) -> str:
    _ = alert_kind
    error = (error_message or "").strip() or "HTTP error"
    return (
        f"{TITLE_HTTP_ERROR}\n"
        f"source: {source}\n"
        f"amount: {format_amount_line(amount_cents)}\n"
        f"error: {error}"
    )


def recent_ingest_alert_exists(
    *,
    source: str,
    alert_kind: AlertKind,
    dedupe_key: str,
    exclude_id: int | None,
    now: datetime | None = None,
) -> bool:
    """True if a prior HTTP-error audit row in the window would have triggered an alert."""
    _ = alert_kind
    cutoff = (now or datetime.now(timezone.utc)) - DEDUPE_WINDOW
    try:
        with get_db() as session:
            q = session.query(WebhookIngestRequest).filter(
                WebhookIngestRequest.source == source,
                WebhookIngestRequest.created_at >= cutoff,
                WebhookIngestRequest.http_status_code >= 400,
            )
            if exclude_id is not None:
                q = q.filter(WebhookIngestRequest.id != exclude_id)

            if dedupe_key.startswith("ext:"):
                external = dedupe_key[4:]
                q = q.filter(WebhookIngestRequest.source_external_id == external)
            elif dedupe_key.startswith("stripe:"):
                stripe_cs = dedupe_key[7:]
                q = q.filter(
                    WebhookIngestRequest.stripe_checkout_session_id == stripe_cs
                )
            elif dedupe_key.startswith("pay:"):
                pay_id = int(dedupe_key[4:])
                q = q.filter(WebhookIngestRequest.payment_id == pay_id)
            else:
                q = q.filter(
                    and_(
                        or_(
                            WebhookIngestRequest.source_external_id.is_(None),
                            WebhookIngestRequest.source_external_id == "",
                        ),
                        or_(
                            WebhookIngestRequest.stripe_checkout_session_id.is_(None),
                            WebhookIngestRequest.stripe_checkout_session_id == "",
                        ),
                        WebhookIngestRequest.payment_id.is_(None),
                    )
                )

            return q.first() is not None
    except Exception:
        logger.warning(
            "payment_ingest_escalation: dedupe lookup failed source=%s kind=%s",
            source,
            ALERT_HTTP_ERROR,
            exc_info=True,
        )
        return False


async def maybe_notify_payment_ingest_escalation(
    *,
    source: str,
    http_status_code: int,
    ctx: Any,
    recorded_id: int | None,
) -> bool:
    """Notify head-admin Slack when ingest fails (HTTP >= 400). Never raises."""
    try:
        alert_kind = should_alert_ingest(
            http_status_code=http_status_code,
            response_json=getattr(ctx, "response_json", None),
        )
        if alert_kind is None:
            return False
        if is_test_ingest(
            is_test=getattr(ctx, "is_test", None),
            request_body=getattr(ctx, "request_body", None),
        ):
            return False

        dedupe_key = ingest_dedupe_key(
            source_external_id=getattr(ctx, "source_external_id", None),
            stripe_checkout_session_id=getattr(
                ctx, "stripe_checkout_session_id", None
            ),
            payment_id=getattr(ctx, "payment_id", None),
        )
        if recent_ingest_alert_exists(
            source=source,
            alert_kind=alert_kind,
            dedupe_key=dedupe_key,
            exclude_id=recorded_id,
        ):
            logger.info(
                "payment_ingest_escalation: deduped source=%s kind=%s key=%s",
                source,
                alert_kind,
                dedupe_key,
            )
            return False

        text = format_ingest_escalation_text(
            alert_kind=alert_kind,
            source=source,
            amount_cents=getattr(ctx, "amount_cents", None),
            error_message=getattr(ctx, "error_message", None),
        )
        from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

        return await notify_slack_head_admin_escalation(text, source=SOURCE_HTTP_ERROR)
    except Exception:
        logger.warning(
            "payment_ingest_escalation: notify failed source=%s",
            source,
            exc_info=True,
        )
        return False
