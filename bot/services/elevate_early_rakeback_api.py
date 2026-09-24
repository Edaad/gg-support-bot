"""Async client for the Elevate (aon-beta) early rakeback bot API.

Four endpoints. ``bot/agency`` is a read-only pre-check for members ClubGG tagged
with an upline. The money path is unchanged: ``bot/quote`` says what a member is
owed for a rake figure, ``bot/record`` writes it to the early rakeback ledger,
and ``DELETE bot/record`` undoes that write if the downstream chip deposit
fails. The rakeback maths lives entirely on the Elevate side — we never compute
money here, and ``record`` recomputes the quote server-side and ignores any
amount we send (``expected_amount`` is only a staleness check).

Config reuses the env vars the audit sync already needs (``AON_BETA_BASE_URL``
includes the ``/api`` prefix). Nothing here raises: every call returns a typed
result the caller branches on, so a flaky ledger can never take down ``/earlyrb``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Quote reason codes (see the early rakeback bot API docs).
REASON_OK = "ok"
REASON_NOT_LISTED_STANDARD_DISABLED = "not_listed_standard_disabled"
REASON_NON_POSITIVE_AMOUNT = "non_positive_amount"
REASON_NOTHING_REMAINING = "nothing_remaining"

# Agency reason codes.
AGENCY_REASON_OK = "ok"
AGENCY_REASON_NO_WEEK_HISTORY = "no_week_history"
AGENCY_REASON_NO_AGENT_OR_SUPER_AGENT = "no_agent_or_super_agent"

# Record outcome codes we branch on.
CODE_OK = "ok"
CODE_ALREADY_RECORDED = "already_recorded"
CODE_AMOUNT_CHANGED = "amount_changed"
CODE_RECORD_IN_PROGRESS = "record_in_progress"
CODE_BELOW_MINIMUM = "below_minimum_threshold"
CODE_ALREADY_DELETED = "already_deleted"
CODE_NOT_BOT_RECORD = "not_bot_record"
CODE_MISSING_RECORD_REFERENCE = "missing_record_reference"

_DEFAULT_TIMEOUT_SEC = 30.0


@dataclass(frozen=True)
class Quote:
    """A successful ``bot/quote`` response."""

    eligible: bool
    reason: str
    remaining: Decimal
    below_minimum: bool
    minimum_threshold: Optional[Decimal]
    display_decimal_places: int
    gg_id: Optional[str] = None
    display_id: Optional[str] = None
    nickname: Optional[str] = None
    member_type: Optional[str] = None
    source: Optional[str] = None
    deal_type: Optional[str] = None
    percentage: Optional[Decimal] = None
    total_already_given: Optional[Decimal] = None
    on_trial: bool = False
    warnings: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class QuoteResult:
    """Outcome of a quote attempt. ``quote`` is set only when ``ok``."""

    ok: bool
    error_code: str = ""
    detail: str = ""
    quote: Optional[Quote] = None


@dataclass(frozen=True)
class RecordResult:
    """Outcome of a record attempt.

    ``ok`` means the amount is on the ledger — including the idempotent replay
    (``already_recorded``), which writes nothing but confirms the original.
    """

    ok: bool
    code: str = ""
    detail: str = ""
    duplicate: bool = False
    amount_recorded: Optional[Decimal] = None
    total_given: Optional[Decimal] = None
    record_id: Optional[str] = None
    entry_id: Optional[str] = None
    quote: Optional[Quote] = None


@dataclass(frozen=True)
class AgencyPerson:
    """An agent or super-agent ID from ``bot/agency``."""

    gg_id: Optional[str] = None
    display_id: Optional[str] = None
    nickname: Optional[str] = None


@dataclass(frozen=True)
class Agency:
    """A successful ``bot/agency`` response."""

    found: bool
    reason: str
    excluded: bool
    gg_id: Optional[str] = None
    display_id: Optional[str] = None
    nickname: Optional[str] = None
    week_start: Optional[str] = None
    week_end: Optional[str] = None
    agent: Optional[AgencyPerson] = None
    super_agent: Optional[AgencyPerson] = None
    standard_player_rate_enabled: bool = False
    exclude_reasons: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class AgencyResult:
    """Outcome of an agency lookup. ``agency`` is set only when ``ok``."""

    ok: bool
    error_code: str = ""
    detail: str = ""
    agency: Optional[Agency] = None


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of a record undo.

    ``ok`` means the ledger is clean — including ``already_deleted``, so a
    timeout retry does not need special-casing.
    """

    ok: bool
    code: str = ""
    detail: str = ""
    amount_removed: Optional[Decimal] = None
    entry_deleted: bool = False
    record_id: Optional[str] = None


class _Config:
    __slots__ = ("base_url", "api_key", "timeout_sec")

    def __init__(self, base_url: str, api_key: str, timeout_sec: float) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_sec = timeout_sec


def load_config() -> Optional[_Config]:
    """Read env config. Returns None when Elevate is not configured on this dyno."""
    base_url = (os.getenv("AON_BETA_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("AON_BETA_INTERNAL_API_KEY") or "").strip()
    if not base_url or not api_key:
        return None
    raw_timeout = (os.getenv("AON_BETA_TIMEOUT_SEC") or "").strip()
    try:
        timeout_sec = float(raw_timeout) if raw_timeout else _DEFAULT_TIMEOUT_SEC
    except ValueError:
        timeout_sec = _DEFAULT_TIMEOUT_SEC
    return _Config(base_url, api_key, timeout_sec)


def early_rakeback_api_configured() -> bool:
    """True when the Elevate early rakeback API is configured on this worker."""
    return load_config() is not None


def _headers(cfg: _Config) -> dict[str, str]:
    return {"X-Internal-Api-Key": cfg.api_key}


def _decimal(raw: Any) -> Optional[Decimal]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _amount_str(amount: Decimal) -> str:
    """Send money as a plain decimal string so no float rounding sneaks in."""
    return format(amount.quantize(Decimal("0.01")), "f")


def _parse_quote(data: dict[str, Any]) -> Quote:
    warnings = data.get("warnings")
    places = data.get("displayDecimalPlaces")
    try:
        decimal_places = int(places) if places is not None else 2
    except (TypeError, ValueError):
        decimal_places = 2
    return Quote(
        eligible=bool(data.get("eligible")),
        reason=str(data.get("reason") or ""),
        remaining=_decimal(data.get("remaining")) or Decimal("0"),
        below_minimum=bool(data.get("belowMinimum")),
        minimum_threshold=_decimal(data.get("minimumThreshold")),
        display_decimal_places=decimal_places,
        gg_id=data.get("ggId"),
        display_id=data.get("displayId"),
        nickname=data.get("nickname"),
        member_type=data.get("memberType"),
        source=data.get("source"),
        deal_type=data.get("dealType"),
        percentage=_decimal(data.get("percentage")),
        total_already_given=_decimal(data.get("totalAlreadyGiven")),
        on_trial=bool(data.get("onTrial")),
        warnings=tuple(str(w) for w in warnings) if isinstance(warnings, list) else (),
        raw=data,
    )


def _parse_agency_person(raw: Any) -> Optional[AgencyPerson]:
    if not isinstance(raw, dict):
        return None
    return AgencyPerson(
        gg_id=raw.get("ggId"),
        display_id=raw.get("displayId"),
        nickname=raw.get("nickname"),
    )


def _parse_agency(data: dict[str, Any]) -> Agency:
    reasons = data.get("excludeReasons")
    return Agency(
        found=bool(data.get("found")),
        reason=str(data.get("reason") or ""),
        excluded=bool(data.get("excluded")),
        gg_id=data.get("ggId"),
        display_id=data.get("displayId"),
        nickname=data.get("nickname"),
        week_start=data.get("weekStart"),
        week_end=data.get("weekEnd"),
        agent=_parse_agency_person(data.get("agent")),
        super_agent=_parse_agency_person(data.get("superAgent")),
        standard_player_rate_enabled=bool(data.get("standardPlayerRateEnabled")),
        exclude_reasons=(
            tuple(str(r) for r in reasons) if isinstance(reasons, list) else ()
        ),
        raw=data,
    )


def _error_detail(data: dict[str, Any], status_code: int) -> tuple[str, str]:
    """Return (code, human detail) from an Elevate error body."""
    code = str(data.get("code") or data.get("error") or f"http_{status_code}")
    detail = str(data.get("error") or data.get("code") or f"HTTP {status_code}")
    return code, detail


async def lookup_early_rakeback_agency(
    *,
    club_slug: str,
    gg_player_id: str,
) -> AgencyResult:
    """GET ``/{club_slug}/early-rakeback/bot/agency``. Never raises.

    Read-only: who this ClubGG ID sat under in the newest processed week they
    appear in, and whether the club's standard-player excludes would pay them 0.
    Does not quote or record.
    """
    cfg = load_config()
    if cfg is None:
        return AgencyResult(False, "not_configured", "Elevate API not configured")

    slug = club_slug.strip().lower()
    params = {"gg_player_id": gg_player_id}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_sec)) as client:
            resp = await client.get(
                f"{cfg.base_url}/{slug}/early-rakeback/bot/agency",
                params=params,
                headers=_headers(cfg),
            )
    except Exception as exc:
        logger.warning(
            "early_rb_agency: request failed slug=%s player=%s err=%s",
            slug,
            gg_player_id,
            type(exc).__name__,
        )
        return AgencyResult(
            False, "request_failed", f"request failed: {type(exc).__name__}"
        )

    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if resp.status_code != 200:
        code, detail = _error_detail(data, resp.status_code)
        logger.warning(
            "early_rb_agency: HTTP %s slug=%s player=%s code=%s",
            resp.status_code,
            slug,
            gg_player_id,
            code,
        )
        return AgencyResult(False, code, detail)

    agency = _parse_agency(data)
    logger.info(
        "early_rb_agency: slug=%s player=%s found=%s reason=%s excluded=%s "
        "excludes=%s week=%s..%s",
        slug,
        gg_player_id,
        agency.found,
        agency.reason,
        agency.excluded,
        ",".join(agency.exclude_reasons) or "-",
        agency.week_start or "-",
        agency.week_end or "-",
    )
    return AgencyResult(True, agency=agency)


async def quote_early_rakeback(
    *,
    club_slug: str,
    gg_player_id: str,
    rake: Decimal,
    pl: Decimal,
    nickname: Optional[str] = None,
) -> QuoteResult:
    """GET ``/{club_slug}/early-rakeback/bot/quote``. Never raises.

    ``rake`` is the member's **total** rake for the current early-RB period, not
    a delta. ``pl`` is always sent so a tax-rebate deal never trips
    ``400 pl_required``.
    """
    cfg = load_config()
    if cfg is None:
        return QuoteResult(False, "not_configured", "Elevate API not configured")

    slug = club_slug.strip().lower()
    params: dict[str, str] = {
        "gg_player_id": gg_player_id,
        "rake": _amount_str(rake),
        "pl": _amount_str(pl),
    }
    if nickname:
        params["nickname"] = nickname

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_sec)) as client:
            resp = await client.get(
                f"{cfg.base_url}/{slug}/early-rakeback/bot/quote",
                params=params,
                headers=_headers(cfg),
            )
    except Exception as exc:
        logger.warning(
            "early_rb_quote: request failed slug=%s player=%s err=%s",
            slug,
            gg_player_id,
            type(exc).__name__,
        )
        return QuoteResult(
            False, "request_failed", f"request failed: {type(exc).__name__}"
        )

    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if resp.status_code != 200:
        code, detail = _error_detail(data, resp.status_code)
        logger.warning(
            "early_rb_quote: HTTP %s slug=%s player=%s code=%s",
            resp.status_code,
            slug,
            gg_player_id,
            code,
        )
        return QuoteResult(False, code, detail)

    quote = _parse_quote(data)
    logger.info(
        "early_rb_quote: slug=%s player=%s eligible=%s reason=%s remaining=%s "
        "below_min=%s warnings=%s",
        slug,
        gg_player_id,
        quote.eligible,
        quote.reason,
        quote.remaining,
        quote.below_minimum,
        ",".join(quote.warnings) or "-",
    )
    return QuoteResult(True, quote=quote)


async def record_early_rakeback(
    *,
    club_slug: str,
    gg_player_id: str,
    rake: Decimal,
    pl: Decimal,
    expected_amount: Decimal,
    idempotency_key: str,
    allow_below_minimum: bool = False,
    nickname: Optional[str] = None,
) -> RecordResult:
    """POST ``/{club_slug}/early-rakeback/bot/record``. Never raises.

    ``expected_amount`` is the ``remaining`` we showed the player: a mismatch
    comes back as ``amount_changed`` rather than a surprise payout. Retrying with
    the same ``idempotency_key`` returns the original record instead of paying
    twice.
    """
    cfg = load_config()
    if cfg is None:
        return RecordResult(False, "not_configured", "Elevate API not configured")

    slug = club_slug.strip().lower()
    body: dict[str, Any] = {
        "gg_player_id": gg_player_id,
        "rake": _amount_str(rake),
        "pl": _amount_str(pl),
        "expected_amount": _amount_str(expected_amount),
        "idempotency_key": idempotency_key,
    }
    if allow_below_minimum:
        body["allow_below_minimum"] = True
    if nickname:
        body["nickname"] = nickname

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_sec)) as client:
            resp = await client.post(
                f"{cfg.base_url}/{slug}/early-rakeback/bot/record",
                json=body,
                headers=_headers(cfg),
            )
    except Exception as exc:
        logger.warning(
            "early_rb_record: request failed slug=%s player=%s key=%s err=%s",
            slug,
            gg_player_id,
            idempotency_key,
            type(exc).__name__,
        )
        return RecordResult(
            False, "request_failed", f"request failed: {type(exc).__name__}"
        )

    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    raw_quote = data.get("quote")
    quote = _parse_quote(raw_quote) if isinstance(raw_quote, dict) else None

    if resp.status_code in (200, 201):
        entry = data.get("entry") if isinstance(data.get("entry"), dict) else {}
        record = data.get("record") if isinstance(data.get("record"), dict) else {}
        result = RecordResult(
            ok=True,
            code=str(data.get("code") or CODE_OK),
            duplicate=bool(data.get("duplicate")),
            amount_recorded=_decimal(data.get("amountRecorded")),
            total_given=_decimal(entry.get("totalGiven")),
            record_id=(str(record.get("_id")) if record.get("_id") else None),
            entry_id=(str(entry.get("_id")) if entry.get("_id") else None),
            quote=quote,
        )
        logger.info(
            "early_rb_record: slug=%s player=%s code=%s duplicate=%s amount=%s key=%s",
            slug,
            gg_player_id,
            result.code,
            result.duplicate,
            result.amount_recorded,
            idempotency_key,
        )
        return result

    code, detail = _error_detail(data, resp.status_code)
    logger.warning(
        "early_rb_record: HTTP %s slug=%s player=%s code=%s key=%s",
        resp.status_code,
        slug,
        gg_player_id,
        code,
        idempotency_key,
    )
    return RecordResult(False, code, detail, quote=quote)


async def delete_early_rakeback(
    *,
    club_slug: str,
    idempotency_key: Optional[str] = None,
    record_id: Optional[str] = None,
) -> DeleteResult:
    """DELETE ``/{club_slug}/early-rakeback/bot/record``. Never raises.

    Identify the line with the ``idempotency_key`` used on the original record,
    or with ``record._id``. A retry after a timeout is safe: ``already_deleted``
    means the ledger is already clean. Only bot-written rows can be removed.
    """
    cfg = load_config()
    if cfg is None:
        return DeleteResult(False, "not_configured", "Elevate API not configured")

    key = (idempotency_key or "").strip() or None
    rid = (record_id or "").strip() or None
    if not key and not rid:
        return DeleteResult(
            False,
            CODE_MISSING_RECORD_REFERENCE,
            "neither idempotency_key nor record_id was sent",
        )

    slug = club_slug.strip().lower()
    params: dict[str, str] = {}
    if key:
        params["idempotency_key"] = key
    if rid:
        params["record_id"] = rid

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_sec)) as client:
            resp = await client.delete(
                f"{cfg.base_url}/{slug}/early-rakeback/bot/record",
                params=params,
                headers=_headers(cfg),
            )
    except Exception as exc:
        logger.warning(
            "early_rb_delete: request failed slug=%s key=%s record_id=%s err=%s",
            slug,
            key,
            rid,
            type(exc).__name__,
        )
        return DeleteResult(
            False, "request_failed", f"request failed: {type(exc).__name__}"
        )

    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if resp.status_code == 200:
        record = data.get("record") if isinstance(data.get("record"), dict) else {}
        result = DeleteResult(
            ok=True,
            code=str(data.get("code") or CODE_OK),
            amount_removed=_decimal(data.get("amountRemoved")),
            entry_deleted=bool(data.get("entryDeleted")),
            record_id=(str(record.get("_id")) if record.get("_id") else rid),
        )
        logger.info(
            "early_rb_delete: slug=%s code=%s key=%s record_id=%s amount=%s",
            slug,
            result.code,
            key,
            result.record_id,
            result.amount_removed,
        )
        return result

    code, detail = _error_detail(data, resp.status_code)
    logger.warning(
        "early_rb_delete: HTTP %s slug=%s code=%s key=%s record_id=%s",
        resp.status_code,
        slug,
        code,
        key,
        rid,
    )
    return DeleteResult(False, code, detail)
