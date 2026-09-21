"""Deposit method weekly threshold alerts (volume / tx count → head-admin Slack)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from api.payments_helpers import (
    OWNER_INGEST_METHODS,
    OWNER_VARIANT_COLUMNS,
    _apply_created_at_range,
    _apply_crypto_paid_at_range,
    aggregate_owner_payment_query,
)
from db.models import CryptoPayment, DepositMethodAlert

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
ALERT_METHODS = frozenset({"venmo", "zelle", "cashapp", "paypal", "crypto"})
MAX_VARIANT_LEN = 255
CONDITION_WEEKLY_VOLUME = "weekly_volume"
CONDITION_WEEKLY_TX_COUNT = "weekly_transaction_count"
OPERATOR_GTE = "gte"
SLACK_SOURCE = "deposit_method_alert"
DASHBOARD_PUBLIC_URL_ENV = "DASHBOARD_PUBLIC_URL"

METHOD_LABELS = {
    "venmo": "Venmo",
    "zelle": "Zelle",
    "cashapp": "Cash App",
    "paypal": "PayPal",
    "crypto": "Crypto",
}


@dataclass(frozen=True)
class EasternWeek:
    week_id: str
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class WeekStats:
    volume_cents: int
    tx_count: int


@dataclass(frozen=True)
class ConditionSpec:
    type: str
    label: str
    validate: Callable[[Any], int]
    is_met: Callable[[dict, WeekStats], bool]
    format_threshold: Callable[[int], str]
    format_current: Callable[[WeekStats], str]


def _usd_to_cents(value: Any) -> int:
    try:
        d = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Volume threshold must be a number") from exc
    if d < Decimal("0.01"):
        raise ValueError("Volume threshold must be at least $0.01")
    cents = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents < 1:
        raise ValueError("Volume threshold must be at least $0.01")
    return cents


def _positive_int(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Transaction count threshold must be an integer") from exc
    if n < 1:
        raise ValueError("Transaction count threshold must be at least 1")
    return n


def _fmt_usd_cents(cents: int) -> str:
    return f"${(Decimal(cents) / 100):,.2f}"


CONDITION_REGISTRY: dict[str, ConditionSpec] = {
    CONDITION_WEEKLY_VOLUME: ConditionSpec(
        type=CONDITION_WEEKLY_VOLUME,
        label="Weekly volume",
        validate=_usd_to_cents,
        is_met=lambda c, s: s.volume_cents >= int(c["threshold"]),
        format_threshold=lambda t: f"≥ {_fmt_usd_cents(t)}",
        format_current=lambda s: _fmt_usd_cents(s.volume_cents),
    ),
    CONDITION_WEEKLY_TX_COUNT: ConditionSpec(
        type=CONDITION_WEEKLY_TX_COUNT,
        label="Weekly transactions",
        validate=_positive_int,
        is_met=lambda c, s: s.tx_count >= int(c["threshold"]),
        format_threshold=lambda t: f"≥ {t} txs",
        format_current=lambda s: f"{s.tx_count} txs",
    ),
}


def eastern_week_bounds_utc(now: datetime | None = None) -> EasternWeek:
    """Current America/New_York Mon 00:00 through now, as UTC bounds.

    ``week_id`` is the Eastern Monday date (YYYY-MM-DD). ``end_utc`` is ``now``
    (or slightly past) so "so far this week" includes the latest ingest.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    et = now.astimezone(EASTERN)
    days_from_monday = et.weekday()  # Mon=0 … Sun=6
    monday_et = (et - timedelta(days=days_from_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_utc = monday_et.astimezone(timezone.utc)
    return EasternWeek(
        week_id=monday_et.date().isoformat(),
        start_utc=start_utc,
        end_utc=now,
    )


def normalize_method(method: str) -> str:
    key = (method or "").strip().lower()
    if key not in ALERT_METHODS:
        allowed = ", ".join(sorted(ALERT_METHODS))
        raise ValueError(f"method must be one of: {allowed}")
    return key


def normalize_variant(variant: str) -> str:
    value = (variant or "").strip()
    if not value:
        raise ValueError("variant is required")
    if len(value) > MAX_VARIANT_LEN:
        raise ValueError("variant is too long")
    return value


def conditions_from_api(raw: list | None) -> list[dict]:
    """Validate create/update payload where volume threshold is USD."""
    if not raw:
        raise ValueError("At least one condition is required")
    if not isinstance(raw, list):
        raise ValueError("conditions must be a list")

    seen: set[str] = set()
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each condition must be an object")
        ctype = str(item.get("type") or "").strip()
        if ctype not in CONDITION_REGISTRY:
            allowed = ", ".join(sorted(CONDITION_REGISTRY))
            raise ValueError(f"Unknown condition type '{ctype}'. Allowed: {allowed}")
        if ctype in seen:
            raise ValueError(f"At most one '{ctype}' condition is allowed")
        seen.add(ctype)

        operator = str(item.get("operator") or OPERATOR_GTE).strip().lower()
        if operator != OPERATOR_GTE:
            raise ValueError(f"Unsupported operator '{operator}' (only gte)")

        spec = CONDITION_REGISTRY[ctype]
        if ctype == CONDITION_WEEKLY_VOLUME:
            usd = item.get("threshold_usd", item.get("threshold"))
            threshold = spec.validate(usd)
        else:
            threshold = spec.validate(item.get("threshold"))

        out.append(
            {
                "type": ctype,
                "operator": OPERATOR_GTE,
                "threshold": threshold,
            }
        )
    return out


def conditions_met(conditions: list[dict], stats: WeekStats) -> bool:
    """True when any condition is met."""
    if not conditions:
        return False
    for cond in conditions:
        spec = CONDITION_REGISTRY.get(str(cond.get("type") or ""))
        if spec is None:
            continue
        if spec.is_met(cond, stats):
            return True
    return False


def week_stats_for(
    session: Session,
    *,
    method: str,
    variant: str,
    week: EasternWeek | None = None,
) -> WeekStats:
    method_slug = normalize_method(method)
    variant_value = normalize_variant(variant)
    week = week or eastern_week_bounds_utc()
    payment_cls = OWNER_INGEST_METHODS[method_slug]
    variant_col = getattr(payment_cls, OWNER_VARIANT_COLUMNS[method_slug])

    query = session.query(payment_cls).filter(
        payment_cls.is_test.is_(False),
        variant_col == variant_value,
    )
    if payment_cls is CryptoPayment:
        query = _apply_crypto_paid_at_range(
            query, from_dt=week.start_utc, to_dt=week.end_utc
        )
    else:
        query = _apply_created_at_range(
            query, payment_cls, from_dt=week.start_utc, to_dt=week.end_utc
        )
    total_count, total_amount_cents = aggregate_owner_payment_query(
        query, payment_cls.amount_cents
    )
    return WeekStats(volume_cents=total_amount_cents, tx_count=total_count)


def distinct_variants_for_method(session: Session, method: str) -> list[str]:
    method_slug = normalize_method(method)
    payment_cls = OWNER_INGEST_METHODS[method_slug]
    variant_col = getattr(payment_cls, OWNER_VARIANT_COLUMNS[method_slug])
    rows = (
        session.query(variant_col)
        .filter(
            payment_cls.is_test.is_(False),
            variant_col.isnot(None),
            variant_col != "",
        )
        .distinct()
        .order_by(variant_col.asc())
        .all()
    )
    return [str(row[0]) for row in rows if row[0]]


def week_stats_map(
    session: Session,
    pairs: list[tuple[str, str]],
    *,
    week: EasternWeek | None = None,
) -> dict[tuple[str, str], WeekStats]:
    """Aggregate once per unique (method, variant)."""
    week = week or eastern_week_bounds_utc()
    out: dict[tuple[str, str], WeekStats] = {}
    for method, variant in pairs:
        key = (method, variant)
        if key in out:
            continue
        out[key] = week_stats_for(session, method=method, variant=variant, week=week)
    return out


def _dashboard_alerts_url() -> str | None:
    raw = (os.getenv(DASHBOARD_PUBLIC_URL_ENV) or "").strip().rstrip("/")
    if not raw:
        return None
    return f"{raw}/alerts"


def format_slack_message(
    alert: DepositMethodAlert,
    *,
    stats: WeekStats,
    week: EasternWeek,
) -> str:
    method_label = METHOD_LABELS.get(alert.method, alert.method)
    lines = [
        ":bell: Deposit method alert",
        "",
        f"*{alert.name}*",
        f"Method: {method_label}",
        f"Variant: `{alert.variant}`",
        f"Week: {week.week_id} (Mon–Sun ET, so far)",
        "",
        "Conditions:",
    ]
    for cond in alert.conditions or []:
        ctype = str(cond.get("type") or "")
        spec = CONDITION_REGISTRY.get(ctype)
        if spec is None:
            lines.append(f"• {ctype}: ?")
            continue
        threshold = int(cond.get("threshold") or 0)
        met = "✓" if spec.is_met(cond, stats) else "✗"
        lines.append(
            f"• {met} {spec.label}: {spec.format_current(stats)} "
            f"(threshold {spec.format_threshold(threshold)})"
        )
    lines.extend(
        [
            "",
            f"This week: {_fmt_usd_cents(stats.volume_cents)} · {stats.tx_count} txs",
        ]
    )
    url = _dashboard_alerts_url()
    if url:
        lines.extend(["", f"<{url}|Open Alerts>"])
    return "\n".join(lines)


async def evaluate_deposit_method_alerts(
    session: Session,
    *,
    method: str,
    variant: str,
    now: datetime | None = None,
) -> int:
    """Evaluate active alerts for method+variant; fire Slack at most once/week.

    Returns the number of alerts that successfully posted.
    Holds row locks while posting so concurrent ingest cannot double-fire.
    """
    method_slug = normalize_method(method)
    variant_value = normalize_variant(variant)
    week = eastern_week_bounds_utc(now)
    stats = week_stats_for(
        session, method=method_slug, variant=variant_value, week=week
    )

    alerts = (
        session.query(DepositMethodAlert)
        .filter(
            DepositMethodAlert.method == method_slug,
            DepositMethodAlert.variant == variant_value,
            DepositMethodAlert.is_active.is_(True),
        )
        .with_for_update()
        .all()
    )
    if not alerts:
        return 0

    from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

    fired = 0
    fire_at = week.end_utc
    for alert in alerts:
        if alert.last_fired_week_id == week.week_id:
            continue
        conditions = list(alert.conditions or [])
        if not conditions_met(conditions, stats):
            continue
        text = format_slack_message(alert, stats=stats, week=week)
        ok = await notify_slack_head_admin_escalation(text, source=SLACK_SOURCE)
        if not ok:
            logger.warning(
                "deposit_method_alert: slack failed alert_id=%s method=%s variant=%r",
                alert.id,
                method_slug,
                variant_value,
            )
            continue
        alert.last_fired_week_id = week.week_id
        alert.last_fired_at = fire_at
        fired += 1
        logger.info(
            "deposit_method_alert: fired alert_id=%s week=%s method=%s variant=%r",
            alert.id,
            week.week_id,
            method_slug,
            variant_value,
        )
    return fired


async def maybe_evaluate_after_ingest(
    *,
    method: str,
    variant: str,
    created: bool,
    is_test: bool,
) -> None:
    """Best-effort evaluate after payment ingest; never raises."""
    if not created or is_test:
        return
    variant_value = (variant or "").strip()
    if not variant_value:
        return
    try:
        from db.connection import get_db

        with get_db() as session:
            await evaluate_deposit_method_alerts(
                session, method=method, variant=variant_value
            )
    except Exception:
        logger.exception(
            "deposit_method_alert: evaluate after ingest failed method=%s variant=%r",
            method,
            variant_value,
        )


def conditions_equal(a: list | None, b: list | None) -> bool:
    left = list(a or [])
    right = list(b or [])
    if len(left) != len(right):
        return False
    by_type = {str(c.get("type")): c for c in left}
    for c in right:
        other = by_type.get(str(c.get("type")))
        if other is None:
            return False
        if int(other.get("threshold") or 0) != int(c.get("threshold") or 0):
            return False
        if str(other.get("operator") or "") != str(c.get("operator") or ""):
            return False
    return True


def condition_summaries(conditions: list | None) -> list[dict]:
    """API-facing condition list with human labels and USD for volume."""
    out: list[dict] = []
    for cond in conditions or []:
        ctype = str(cond.get("type") or "")
        spec = CONDITION_REGISTRY.get(ctype)
        threshold = int(cond.get("threshold") or 0)
        row: dict[str, Any] = {
            "type": ctype,
            "operator": str(cond.get("operator") or OPERATOR_GTE),
            "threshold": threshold,
            "label": spec.label if spec else ctype,
            "summary": spec.format_threshold(threshold) if spec else str(threshold),
        }
        if ctype == CONDITION_WEEKLY_VOLUME:
            row["threshold_usd"] = float(Decimal(threshold) / 100)
        out.append(row)
    return out
