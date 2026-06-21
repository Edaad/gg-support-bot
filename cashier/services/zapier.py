"""Zapier webhook for completed GGCashier cashouts."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from bot.services.club import get_method_by_id, get_sub_option_by_id
from bot.services.player_details import parse_tracking_title
from bot.services.staff_cashout_records import get_staff_cashout_record

logger = logging.getLogger(__name__)

ZAPIER_CASHOUT_WEBHOOK_ENV = "ZAPIER_CASHOUT_WEBHOOK_URL"
DEFAULT_WEBHOOK_URL = (
    "https://hooks.zapier.com/hooks/catch/20524789/4ogfszq/"
)

METHOD_FIELDS = (
    "venmo",
    "cashapp",
    "zelle",
    "crypto",
    "paypal",
    "revolut",
    "other",
)


def build_zapier_name(group_title: str) -> Optional[str]:
    """Build CLUB / PLAYER_ID / PLAYER_NAME from group title."""
    parsed = parse_tracking_title(group_title)
    if not parsed:
        return None
    shorthand, gg_player_id = parsed
    parts = [p.strip() for p in group_title.split("/") if p.strip()]
    player_name = parts[2] if len(parts) >= 3 else ""
    return f"{shorthand} / {gg_player_id} / {player_name}"


def _slug_to_field(slug: str) -> str:
    normalized = (slug or "").strip().lower()
    if normalized in METHOD_FIELDS:
        return normalized
    return "other"


def _primary_payment(payments: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not payments:
        return None
    return sorted(payments, key=lambda p: p.get("sort_order", 0))[0]


def build_zapier_payload_from_cashout_record(
    record: dict[str, Any],
    payments: list[dict[str, Any]],
    *,
    trade_record_checked: bool = True,
) -> tuple[Optional[dict], Optional[str]]:
    """Return (payload, error_message) for a staff cashout record."""
    name = build_zapier_name(record.get("group_title") or "")
    if not name:
        return None, (
            "Cannot send to Glide: group title must match "
            "CLUB / PLAYER_ID / PLAYER_NAME (e.g. RT / 2427-3267 / Samin)."
        )

    primary = _primary_payment(payments)
    if not primary:
        return None, "Cannot send to Glide: no payment method on record."

    method_id = primary.get("payment_method_id")
    slug = "other"
    if method_id:
        method = get_method_by_id(int(method_id))
        if method:
            slug = method.get("slug") or "other"

    field = _slug_to_field(slug)
    payout = (primary.get("payout_details") or "").strip()
    method_values = {f: "" for f in METHOD_FIELDS}
    method_values[field] = payout

    crypto_asset = ""
    if slug == "crypto":
        sub_id = primary.get("payment_sub_option_id")
        if sub_id:
            sub = get_sub_option_by_id(int(sub_id))
            if sub:
                crypto_asset = sub.get("name") or ""

    amount = record.get("amount")
    if isinstance(amount, Decimal):
        opening_balance = float(amount)
    else:
        opening_balance = float(amount or 0)

    now = datetime.now(ZoneInfo("America/New_York"))
    date_time = now.isoformat(timespec="seconds")

    tr_checked = "✅ Checked" if trade_record_checked else ""

    payload = {
        "name": name,
        "opening_balance": opening_balance,
        **method_values,
        "crypto_asset": crypto_asset,
        "reset": False,
        "date_time": date_time,
        "tr_checked": tr_checked,
    }
    return payload, None


def build_zapier_payload(job: dict[str, Any]) -> tuple[Optional[dict], Optional[str]]:
    """Return (payload, error_message) from a cashier job dict."""
    payments = [
        {
            "payment_method_id": job.get("payment_method_id"),
            "payment_sub_option_id": job.get("payment_sub_option_id"),
            "payout_details": job.get("payout_details"),
            "sort_order": 0,
        }
    ]
    record = {
        "group_title": job.get("group_title") or "",
        "amount": job.get("amount"),
    }
    return build_zapier_payload_from_cashout_record(
        record,
        payments,
        trade_record_checked=bool(job.get("trade_record_checked")),
    )


async def _post_zapier_payload(
    payload: dict[str, Any], *, log_context: str
) -> tuple[bool, Optional[str]]:
    url = os.getenv(ZAPIER_CASHOUT_WEBHOOK_ENV) or DEFAULT_WEBHOOK_URL
    if not url:
        logger.info("zapier webhook skipped (no URL) %s", log_context)
        return True, None

    field = next((k for k in METHOD_FIELDS if payload.get(k)), "other")
    logger.info(
        "zapier webhook posting %s name=%r opening_balance=%s field=%s crypto_asset=%r",
        log_context,
        payload.get("name"),
        payload.get("opening_balance"),
        field,
        payload.get("crypto_asset"),
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("zapier webhook ok %s status=%s", log_context, resp.status_code)
        return True, None
    except Exception:
        logger.exception("zapier webhook failed %s", log_context)
        return False, "Zapier webhook failed"


async def fire_zapier_webhook(job: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """POST completed cashout to Zapier from a cashier job. Returns (success, error_message)."""
    job_id = job.get("id")
    payload, err = build_zapier_payload(job)
    if err or not payload:
        logger.warning(
            "zapier payload build failed job_id=%s err=%s",
            job_id,
            err,
        )
        return False, err

    return await _post_zapier_payload(payload, log_context=f"job_id={job_id}")


async def fire_zapier_webhook_for_record(record_id: int) -> tuple[bool, Optional[str]]:
    """POST cashout record to Zapier (used on dashboard edits)."""
    record = get_staff_cashout_record(int(record_id))
    if not record:
        return False, "Cashout record not found."

    payments = record.get("payments") or []
    payload, err = build_zapier_payload_from_cashout_record(record, payments)
    if err or not payload:
        logger.warning(
            "zapier payload build failed record_id=%s err=%s",
            record_id,
            err,
        )
        return False, err

    return await _post_zapier_payload(
        payload, log_context=f"record_id={record_id}"
    )
