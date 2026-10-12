"""Date parameter parsing shared by every tool.

Two conventions coexist in the dashboard: payments filter on UTC datetimes, while
tickets, activity, audits and record exports use America/New_York calendar days.
``parse_when`` accepts both ``YYYY-MM-DD`` (interpreted as an ET day) and ISO-8601
datetimes (naive values are UTC, matching ``api/routes/payments.py``).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from mcp_server.errors import bad_input

ET = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_et() -> date:
    return datetime.now(ET).date()


def parse_et_day(value: str | None, *, default: date | None = None) -> date | None:
    """Parse ``YYYY-MM-DD``; ``None``/empty returns ``default``."""
    raw = (value or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw[:10]) if len(raw) >= 10 else None
    except ValueError:
        pass
    raise bad_input(f"expected a YYYY-MM-DD date, got {value!r}")


def et_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ET).astimezone(timezone.utc)


def et_day_end_utc(day: date) -> datetime:
    """Exclusive end: midnight ET of the following day, in UTC."""
    return et_day_start_utc(day + timedelta(days=1))


def parse_when(
    value: str | None, *, end: bool = False, naive: bool = False
) -> datetime | None:
    """Parse a date-or-datetime filter into an aware (or naive) UTC datetime.

    ``YYYY-MM-DD`` becomes the start of that ET day, or its exclusive end when
    ``end=True``. ISO datetimes without an offset are taken as UTC.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    result: datetime
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            day = date.fromisoformat(raw)
        except ValueError as exc:
            raise bad_input(f"invalid date {value!r}") from exc
        result = et_day_end_utc(day) if end else et_day_start_utc(day)
    else:
        text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise bad_input(
                f"invalid datetime {value!r}; use YYYY-MM-DD or ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        result = parsed.astimezone(timezone.utc)
    if naive:
        return result.replace(tzinfo=None)
    return result


def parse_range(
    from_: str | None, to: str | None, *, naive: bool = False
) -> tuple[datetime | None, datetime | None]:
    start = parse_when(from_, naive=naive)
    stop = parse_when(to, end=True, naive=naive)
    if start and stop and start > stop:
        raise bad_input("from must be on or before to")
    return start, stop
