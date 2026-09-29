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


def _condition_lines(conditions: list | None, stats: WeekStats) -> list[str]:
    lines: list[str] = []
    for cond in conditions or []:
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
    return lines


def format_slack_message(
    alert: DepositMethodAlert,
    *,
    stats: WeekStats,
    week: EasternWeek,
    include_alert: bool = True,
    include_disable: bool = False,
) -> str:
    method_label = METHOD_LABELS.get(alert.method, alert.method)
    title = (
        ":bell: Deposit destination disabled"
        if include_disable and not include_alert
        else ":bell: Deposit method alert"
    )
    lines = [
        title,
        "",
        f"*{alert.name}*",
        f"Method: {method_label}",
        f"Variant: `{alert.variant}`",
        f"Week: {week.week_id} (Mon–Sun ET, so far)",
    ]
    if include_alert:
        lines.extend(["", "Conditions:", *_condition_lines(alert.conditions, stats)])
    if include_disable:
        lines.extend(
            [
                "",
                "This destination is off in every club until Monday, or until an "
                "admin unchecks Disable this destination when reached.",
                "",
                "Disable conditions:",
                *_condition_lines(alert.disable_conditions, stats),
            ]
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


def disable_conditions_met(alert: DepositMethodAlert, stats: WeekStats) -> bool:
    if not alert.disable_enabled:
        return False
    return conditions_met(list(alert.disable_conditions or []), stats)


def disable_status(
    alert: DepositMethodAlert, stats: WeekStats, week_id: str
) -> str | None:
    """Card status: off, pending_slack, on, or None when disable is not in use."""
    if not alert.is_active or not alert.disable_enabled:
        return None
    met = conditions_met(list(alert.disable_conditions or []), stats)
    latched = alert.last_disable_fired_week_id == week_id
    if latched and met:
        return "off"
    if met:
        return "pending_slack"
    return "on"


def destination_is_disabled(
    alert: DepositMethodAlert, stats: WeekStats, week_id: str
) -> bool:
    return disable_status(alert, stats, week_id) == "off"


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
        alert_due = alert.last_fired_week_id != week.week_id and conditions_met(
            list(alert.conditions or []), stats
        )
        disable_due = (
            bool(alert.disable_enabled)
            and alert.last_disable_fired_week_id != week.week_id
            and conditions_met(list(alert.disable_conditions or []), stats)
        )
        if not alert_due and not disable_due:
            continue
        text = format_slack_message(
            alert,
            stats=stats,
            week=week,
            include_alert=alert_due,
            include_disable=disable_due,
        )
        ok = await notify_slack_head_admin_escalation(text, source=SLACK_SOURCE)
        if not ok:
            logger.warning(
                "deposit_method_alert: slack failed alert_id=%s method=%s variant=%r "
                "alert_due=%s disable_due=%s",
                alert.id,
                method_slug,
                variant_value,
                alert_due,
                disable_due,
            )
            continue
        if alert_due:
            alert.last_fired_week_id = week.week_id
            alert.last_fired_at = fire_at
        if disable_due:
            alert.last_disable_fired_week_id = week.week_id
            alert.last_disable_fired_at = fire_at
        fired += 1
        logger.info(
            "deposit_method_alert: fired alert_id=%s week=%s method=%s variant=%r "
            "alert_due=%s disable_due=%s",
            alert.id,
            week.week_id,
            method_slug,
            variant_value,
            alert_due,
            disable_due,
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


def _variant_field(variant: Any, name: str) -> Any:
    if isinstance(variant, dict):
        return variant.get(name)
    return getattr(variant, name, None)


def destination_match_key(method: str, raw: str | None) -> str:
    """Comparable key for an alert variant string."""
    method_slug = normalize_method(method)
    text = raw or ""
    if method_slug == "venmo":
        from bot.services.payment_method_binding import _normalize_venmo_handle

        return _normalize_venmo_handle(text)
    if method_slug == "cashapp":
        from bot.services.payment_method_binding import _normalize_cashapp_handle

        return _normalize_cashapp_handle(text)
    if method_slug == "zelle":
        from bot.services.payment_method_binding import canonicalize_zelle_recipient

        return canonicalize_zelle_recipient(text)
    if method_slug == "paypal":
        from bot.services.payment_method_binding import normalize_paypal_email

        return normalize_paypal_email(text)
    return text.strip().lower()


def _variant_text_blob(variant: Any) -> str:
    parts = [
        _variant_field(variant, name)
        for name in (
            "label",
            "response_text",
            "response_caption",
            "venmo_tag",
            "venmo_link",
            "cashapp_tag",
            "cashapp_link",
        )
    ]
    return "\n".join(str(part) for part in parts if isinstance(part, str))


def variant_match_key(method: str, variant: Any) -> str | None:
    """Destination key stored on a club tier variant, if one can be read."""
    method_slug = normalize_method(method)
    if method_slug == "venmo":
        from bot.services.payment_method_binding import (
            _normalize_venmo_handle,
            extract_venmo_handle_from_text,
        )

        tag = _variant_field(variant, "venmo_tag")
        if isinstance(tag, str) and tag.strip():
            handle = extract_venmo_handle_from_text(tag) or _normalize_venmo_handle(tag)
            return handle or None
        for name in ("response_text", "response_caption", "venmo_link"):
            handle = extract_venmo_handle_from_text(_variant_field(variant, name))
            if handle:
                return handle
        return None
    if method_slug == "cashapp":
        from bot.services.payment_method_binding import (
            _normalize_cashapp_handle,
            extract_cashapp_handle_from_text,
        )

        tag = _variant_field(variant, "cashapp_tag")
        if isinstance(tag, str) and tag.strip():
            return _normalize_cashapp_handle(tag) or None
        for name in ("response_text", "response_caption", "cashapp_link"):
            handle = extract_cashapp_handle_from_text(_variant_field(variant, name))
            if handle:
                return handle
        return None
    if method_slug == "zelle":
        from bot.services.payment_method_binding import (
            extract_zelle_recipient_from_text,
        )

        for name in ("response_text", "response_caption"):
            recipient = extract_zelle_recipient_from_text(_variant_field(variant, name))
            if recipient:
                return recipient
        return None
    if method_slug == "paypal":
        from bot.services.payment_method_binding import extract_paypal_email_from_text

        for name in ("response_text", "response_caption"):
            email = extract_paypal_email_from_text(_variant_field(variant, name))
            if email:
                return email
        return None
    return None


def destination_matches(method: str, variant: Any, key: str) -> bool:
    if not key:
        return False
    method_slug = normalize_method(method)
    if method_slug == "crypto":
        return key in _variant_text_blob(variant).lower()
    return variant_match_key(method_slug, variant) == key


def is_destination_disabled(method: str, variant: Any, disabled_keys: set[str]) -> bool:
    return any(destination_matches(method, variant, key) for key in disabled_keys)


def disabled_destination_keys(
    session: Session,
    method: str,
    *,
    now: datetime | None = None,
) -> set[str]:
    """Keys currently skipped for this method. Empty when nothing is latched and met."""
    try:
        method_slug = normalize_method(method)
    except ValueError:
        return set()
    week = eastern_week_bounds_utc(now)
    rows = (
        session.query(DepositMethodAlert)
        .filter(
            DepositMethodAlert.method == method_slug,
            DepositMethodAlert.is_active.is_(True),
            DepositMethodAlert.disable_enabled.is_(True),
            DepositMethodAlert.last_disable_fired_week_id == week.week_id,
        )
        .all()
    )
    keys: set[str] = set()
    for row in rows:
        stats = week_stats_for(
            session, method=method_slug, variant=row.variant, week=week
        )
        if not conditions_met(list(row.disable_conditions or []), stats):
            continue
        key = destination_match_key(method_slug, row.variant)
        if key:
            keys.add(key)
    return keys


def clubs_for_destinations(
    session: Session,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Club id/name list for each method+variant pair that a deposit variant matches."""
    from db.models import Club, ClubPaymentMethod, ClubPaymentTierVariant

    wanted: dict[str, list[tuple[str, str]]] = {}
    for method, variant in pairs:
        try:
            method_slug = normalize_method(method)
        except ValueError:
            continue
        key = destination_match_key(method_slug, variant)
        wanted.setdefault(method_slug, []).append((variant, key))
    if not wanted:
        return {}

    rows = (
        session.query(
            Club.id, Club.name, ClubPaymentMethod.slug, ClubPaymentTierVariant
        )
        .join(
            ClubPaymentMethod,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .join(Club, Club.id == ClubPaymentMethod.club_id)
        .filter(
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.slug.in_(list(wanted)),
        )
        .all()
    )
    by_slug: dict[str, list[tuple[int, str, Any]]] = {}
    for club_id, club_name, slug, variant in rows:
        by_slug.setdefault(str(slug), []).append(
            (int(club_id), str(club_name), variant)
        )

    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for method_slug, items in wanted.items():
        for variant, key in items:
            seen: set[int] = set()
            clubs: list[dict[str, Any]] = []
            for club_id, club_name, variant_row in by_slug.get(method_slug, []):
                if club_id in seen or not destination_matches(
                    method_slug, variant_row, key
                ):
                    continue
                seen.add(club_id)
                clubs.append({"id": club_id, "name": club_name})
            clubs.sort(key=lambda club: club["name"].lower())
            out[(method_slug, variant)] = clubs
    return out


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
