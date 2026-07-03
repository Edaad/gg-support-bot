"""Per-club audit day timezone policies for trade records and payment reconciliation."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

_FIXED_UTC_MINUS_4 = timezone(timedelta(hours=-4))
_FIXED_UTC_MINUS_5 = timezone(timedelta(hours=-5))
_AMERICA_NEW_YORK = ZoneInfo("America/New_York")

_PERIOD_TZ_SUFFIX_RE = re.compile(
    r"\(UTC([+-])(\d{1,2}):(\d{2})\)",
    re.IGNORECASE,
)


class AuditTimezonePolicy(str, Enum):
    AMERICA_NEW_YORK = "AMERICA_NEW_YORK"  # legacy / payment display only
    FIXED_UTC_MINUS_4 = "FIXED_UTC_MINUS_4"
    FIXED_UTC_MINUS_5 = "FIXED_UTC_MINUS_5"


class UnknownClubSlugError(ValueError):
    pass


SLUG_TO_POLICY: dict[str, AuditTimezonePolicy] = {
    "round-table": AuditTimezonePolicy.FIXED_UTC_MINUS_4,
    "creator-club": AuditTimezonePolicy.FIXED_UTC_MINUS_4,
    "clubgto": AuditTimezonePolicy.FIXED_UTC_MINUS_5,
    "aces-table": AuditTimezonePolicy.FIXED_UTC_MINUS_5,
}

POLICY_LABELS: dict[AuditTimezonePolicy, str] = {
    AuditTimezonePolicy.AMERICA_NEW_YORK: "ET",
    AuditTimezonePolicy.FIXED_UTC_MINUS_4: "UTC-4",
    AuditTimezonePolicy.FIXED_UTC_MINUS_5: "UTC-5",
}


def zone_for_policy(policy: AuditTimezonePolicy):
    return _zone_for_policy(policy)


def zone_for_slug(slug: str):
    return _zone_for_policy(audit_timezone_for_slug(slug))


def _zone_for_policy(policy: AuditTimezonePolicy):
    if policy == AuditTimezonePolicy.AMERICA_NEW_YORK:
        return _AMERICA_NEW_YORK
    if policy == AuditTimezonePolicy.FIXED_UTC_MINUS_4:
        return _FIXED_UTC_MINUS_4
    return _FIXED_UTC_MINUS_5


def zone_for_payment_display():
    """America/New_York for payment timestamps in audit export (all clubs)."""
    return _AMERICA_NEW_YORK


def audit_timezone_for_slug(slug: str) -> AuditTimezonePolicy:
    key = slug.strip().lower()
    policy = SLUG_TO_POLICY.get(key)
    if policy is None:
        raise UnknownClubSlugError(f"Unknown club slug for audit timezone: {slug!r}")
    return policy


def audit_timezone_label(policy: AuditTimezonePolicy) -> str:
    return POLICY_LABELS[policy]


def _parse_date_arg(date_val: date | str) -> date:
    if isinstance(date_val, date):
        return date_val
    raw = date_val.strip()[:10]
    return date.fromisoformat(raw)


def audit_day_bounds_utc(slug: str, date_val: date | str) -> tuple[datetime, datetime]:
    """Local calendar day → UTC range (start and end inclusive)."""
    policy = audit_timezone_for_slug(slug)
    local_date = _parse_date_arg(date_val)
    tz = _zone_for_policy(policy)
    start_local = datetime(
        local_date.year,
        local_date.month,
        local_date.day,
        tzinfo=tz,
    )
    next_day = local_date + timedelta(days=1)
    end_local = datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        tzinfo=tz,
    ) - timedelta(microseconds=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def audit_day_window_utc(slug: str, date_val: date | str) -> tuple[datetime, datetime]:
    """Local calendar day + first hour of next day (export grace window)."""
    policy = audit_timezone_for_slug(slug)
    local_date = _parse_date_arg(date_val)
    tz = _zone_for_policy(policy)
    start_local = datetime(
        local_date.year,
        local_date.month,
        local_date.day,
        tzinfo=tz,
    )
    next_day = local_date + timedelta(days=1)
    end_local = datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        1,
        0,
        0,
        tzinfo=tz,
    ) - timedelta(microseconds=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def union_audit_day_window_utc(date_val: date | str) -> tuple[datetime, datetime]:
    """Widest UTC window spanning all club policies for one audit date."""
    starts: list[datetime] = []
    ends: list[datetime] = []
    for slug in SLUG_TO_POLICY:
        start, end = audit_day_window_utc(slug, date_val)
        starts.append(start)
        ends.append(end)
    return min(starts), max(ends)


def parse_row_datetime(
    raw: Any,
    audit_date: date,
    policy: AuditTimezonePolicy,
) -> datetime | None:
    """Trade record row datetime → aware UTC."""
    tz = _zone_for_policy(policy)

    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        return dt.astimezone(timezone.utc)

    if isinstance(raw, datetime):
        return _to_utc(raw)

    s = str(raw).strip() if raw is not None else ""
    if not s:
        return None

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ):
        try:
            parsed = datetime.strptime(s[:19] if " " in s else s, fmt)
            return _to_utc(parsed)
        except ValueError:
            continue

    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            local = datetime(
                audit_date.year,
                audit_date.month,
                audit_date.day,
                t.hour,
                t.minute,
                t.second,
                tzinfo=tz,
            )
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def occurred_at_in_audit_day(
    ts: datetime,
    slug: str,
    date_val: date | str,
) -> bool:
    """True if ts falls within the audit export window for slug + date."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    start, end = audit_day_window_utc(slug, date_val)
    return start <= ts <= end


def period_timezone_warning(
    period_text: str,
    slug: str,
) -> str | None:
    """Warn when Period suffix timezone disagrees with slug policy (non-blocking)."""
    match = _PERIOD_TZ_SUFFIX_RE.search(period_text or "")
    if not match:
        return None

    sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3))
    offset_minutes = hours * 60 + minutes
    if sign == "-":
        offset_minutes = -offset_minutes

    policy = audit_timezone_for_slug(slug)
    if policy == AuditTimezonePolicy.FIXED_UTC_MINUS_5:
        expected_minutes = -5 * 60
    elif policy == AuditTimezonePolicy.FIXED_UTC_MINUS_4:
        expected_minutes = -4 * 60
    else:
        # America/New_York varies with DST; suffix is informational only.
        return None

    if offset_minutes != expected_minutes:
        label = audit_timezone_label(policy)
        return (
            f"Period suffix timezone (UTC{sign}{hours}:{minutes:02d}) "
            f"differs from {slug!r} policy ({label})."
        )
    return None
