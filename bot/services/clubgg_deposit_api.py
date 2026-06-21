"""Optional bridge from /add to the ClubGG deposit bot's remote-trigger HTTP API.

When a club has ``auto_chip_adding_enabled`` turned on in the dashboard, an admin
``/add <amount>`` in a linked support group will (in addition to the existing
confirmation behaviour, which is unchanged) POST a deposit to the ClubGG deposit
bot so chips are added automatically.

Design goals:
- **Purely additive / fail-safe.** If the feature is off, the API is not
  configured, or anything cannot be resolved, the existing ``/add`` behaviour is
  untouched and no chips are sent. The customer-facing confirmation never changes.
- **Idempotent.** ``request_id`` is derived from the Telegram message id so the
  same ``/add`` can never double-send (and the two internal /add code paths can
  never both fire a deposit).
- **Round Table vs Aces Table** is resolved from the customer's last ``/deposit``
  union choice (persisted on the group), never guessed.

All configuration is via environment variables (see ``.env.example``). The
per-club on/off switch lives in the database (``clubs.auto_chip_adding_enabled``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from bot.services.club import (
    get_auto_chip_adding_enabled,
    get_club_by_id,
    get_group_title_for_chat,
    get_last_deposit_union,
)
from bot.services.player_details import gg_player_id_from_title
from bot.services.round_table_unions import ROUND_TABLE_CLUB_NAME

logger = logging.getLogger(__name__)

# ClubGG internal club ids (for reference / logging). The deposit API accepts the
# club *name* (matching its clubs.json) which is what we send.
CLUBGG_CLUB_IDS = {
    "ClubGTO": "790203",
    "Round Table": "522594",
    "Aces Table": "983183",
    "Creator Club": "846162",
}

# Canonical dashboard clubs.name (lowercased) -> ClubGG club name for non-union clubs.
_CLUBGG_CANONICAL = {
    "clubgto": "ClubGTO",
    "round table": "Round Table",
    "aces table": "Aces Table",
    "creator club": "Creator Club",
}

# Terminal job statuses returned by the deposit API (see its §6).
_TERMINAL_STATUSES = frozenset(
    {"success", "dry_run", "skipped", "cancelled", "fail", "uncertain", "error"}
)
_PROBLEM_STATUSES = frozenset({"fail", "uncertain", "error", "cancelled"})

# Idempotency / double-fire guard shared across the PTB and Telethon event loops.
_seen_lock = threading.Lock()
_seen_request_ids: dict[str, float] = {}
_SEEN_TTL_SEC = 600.0


@dataclass(frozen=True)
class _Config:
    base_url: str
    token: str
    dry_run: bool
    alert_chat_id: Optional[int]
    alert_on_success: bool
    expected_host: Optional[str]
    expected_profile: Optional[str]
    union_max_age_hours: float
    timeout_sec: float
    poll_interval_sec: float
    poll_timeout_sec: float


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _env_int_optional(key: str) -> Optional[int]:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def load_config() -> Optional[_Config]:
    """Read env config. Returns None when the feature is not configured."""
    base_url = (os.getenv("GG_DEPOSIT_API_BASE_URL") or "").strip().rstrip("/")
    token = (os.getenv("GG_DEPOSIT_API_TOKEN") or "").strip()
    if not base_url or not token:
        return None
    return _Config(
        base_url=base_url,
        token=token,
        dry_run=_env_bool("GG_DEPOSIT_API_DRY_RUN", False),
        alert_chat_id=_env_int_optional("GG_DEPOSIT_API_ALERT_CHAT_ID"),
        alert_on_success=_env_bool("GG_DEPOSIT_API_ALERT_ON_SUCCESS", False),
        expected_host=(os.getenv("GG_DEPOSIT_API_EXPECTED_HOST") or "").strip() or None,
        expected_profile=(os.getenv("GG_DEPOSIT_API_EXPECTED_PROFILE") or "").strip() or None,
        union_max_age_hours=_env_float("GG_DEPOSIT_API_UNION_MAX_AGE_HOURS", 24.0),
        timeout_sec=_env_float("GG_DEPOSIT_API_TIMEOUT_SEC", 10.0),
        poll_interval_sec=_env_float("GG_DEPOSIT_API_POLL_INTERVAL_SEC", 3.0),
        poll_timeout_sec=_env_float("GG_DEPOSIT_API_POLL_TIMEOUT_SEC", 180.0),
    )


def resolve_clubgg_club_name(
    club_name: Optional[str], union_shorthand: Optional[str]
) -> Optional[str]:
    """Map a dashboard clubs.name (+ RT/AT union) to a ClubGG club name.

    Round Table requires a union: RT -> "Round Table", AT -> "Aces Table".
    Other clubs map directly. Returns None when it cannot be resolved.
    """
    key = (club_name or "").strip().lower()
    if key == ROUND_TABLE_CLUB_NAME.strip().lower():
        u = (union_shorthand or "").strip().upper()
        if u == "RT":
            return "Round Table"
        if u == "AT":
            return "Aces Table"
        return None
    return _CLUBGG_CANONICAL.get(key)


def _format_chip_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return str(int(amount))
    return format(amount.normalize(), "f")


def request_id_for(chat_id: int, message_id: int, *, part: str = "base") -> str:
    """Stable idempotency key. Bonus is a separate transaction with its own id."""
    base = f"tg-{int(chat_id)}-{int(message_id)}"
    if part == "base":
        return base
    return f"{base}-{part}"


def _deposit_transactions(
    amount: Decimal, bonus: Optional[Decimal]
) -> list[tuple[str, Decimal, str]]:
    """Return (label, chip_amount, request_id_part) for each ClubGG deposit."""
    txs: list[tuple[str, Decimal, str]] = [("deposit", amount, "base")]
    if bonus is not None and bonus > 0:
        txs.append(("bonus", bonus, "bonus"))
    return txs


def _claim_request(request_id: str) -> bool:
    """Return True if this request_id was newly claimed (not seen recently)."""
    now = time.monotonic()
    with _seen_lock:
        # prune
        stale = [k for k, ts in _seen_request_ids.items() if now - ts > _SEEN_TTL_SEC]
        for k in stale:
            _seen_request_ids.pop(k, None)
        if request_id in _seen_request_ids:
            return False
        _seen_request_ids[request_id] = now
        return True


def _auth_headers(cfg: _Config) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.token}", "X-API-Key": cfg.token}


async def _send_alert(cfg: _Config, ptb_bot: Any | None, message: str) -> None:
    """Best-effort staff alert to a configured chat; always logs."""
    logger.info("auto_chip_add: %s", message)
    if ptb_bot is None or cfg.alert_chat_id is None:
        return
    try:
        await ptb_bot.send_message(chat_id=cfg.alert_chat_id, text=message)
    except Exception:
        logger.warning("auto_chip_add: failed to send staff alert", exc_info=True)


async def _health_ok(cfg: _Config, client: httpx.AsyncClient) -> tuple[bool, str]:
    """Preflight. Returns (ok, detail). ok=False means do not attempt a deposit."""
    params: dict[str, str] = {}
    if cfg.expected_host:
        params["expected_host"] = cfg.expected_host
    if cfg.expected_profile:
        params["expected_profile"] = cfg.expected_profile
    try:
        resp = await client.get(
            f"{cfg.base_url}/health", headers=_auth_headers(cfg), params=params or None
        )
    except Exception as exc:
        return False, f"no deposit machine online ({type(exc).__name__})"
    if resp.status_code != 200:
        return False, f"health returned HTTP {resp.status_code}"
    try:
        data = resp.json()
    except Exception:
        return False, "health returned non-JSON"
    if (cfg.expected_host or cfg.expected_profile) and data.get("expected_match") is False:
        return (
            False,
            "identity mismatch: live machine is "
            f"{data.get('hostname')}/{data.get('profile')}",
        )
    return True, "ok"


async def _submit_deposit(
    cfg: _Config,
    client: httpx.AsyncClient,
    *,
    club: str,
    player_id: str,
    amount: str,
    request_id: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """POST /deposit. Returns (job_id, status, error_message)."""
    body: dict[str, Any] = {
        "club": club,
        "player_id": player_id,
        "amount": amount,
        "request_id": request_id,
    }
    if cfg.dry_run:
        body["dry_run"] = True
    if cfg.expected_host:
        body["expected_host"] = cfg.expected_host
    if cfg.expected_profile:
        body["expected_profile"] = cfg.expected_profile
    try:
        resp = await client.post(
            f"{cfg.base_url}/deposit", headers=_auth_headers(cfg), json=body
        )
    except Exception as exc:
        return None, None, f"request failed: {type(exc).__name__}"

    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code in (200, 202):
        return data.get("job_id") or request_id, data.get("status"), None
    # Documented error shapes.
    err = data.get("error") or f"HTTP {resp.status_code}"
    detail = err
    if err == "missing_fields":
        detail = f"missing_fields: {data.get('fields')}"
    elif err == "unknown_club":
        detail = f"unknown_club: {data.get('club')} (known: {data.get('known_clubs')})"
    elif err == "identity_mismatch":
        detail = "identity_mismatch (wrong deposit machine)"
    elif resp.status_code == 401:
        detail = "unauthorized (bad API token)"
    return None, None, detail


async def _poll_until_terminal(
    cfg: _Config, client: httpx.AsyncClient, job_id: str
) -> dict[str, Any]:
    """Poll GET /deposit/<job_id> until terminal/timeout. Returns the last job dict."""
    deadline = time.monotonic() + cfg.poll_timeout_sec
    last: dict[str, Any] = {"status": "timeout", "reason": "no terminal status in time"}
    while time.monotonic() < deadline:
        try:
            resp = await client.get(
                f"{cfg.base_url}/deposit/{job_id}", headers=_auth_headers(cfg)
            )
            if resp.status_code == 200:
                last = resp.json()
                status = (last.get("status") or "").lower()
                if status in _TERMINAL_STATUSES:
                    return last
        except Exception:
            logger.debug("auto_chip_add: poll error for job %s", job_id, exc_info=True)
        await asyncio.sleep(cfg.poll_interval_sec)
    return last


def _union_is_fresh(recorded_at: Optional[datetime], max_age_hours: float) -> bool:
    if recorded_at is None:
        return False
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - recorded_at
    return age.total_seconds() <= max_age_hours * 3600.0


async def _run_single_deposit(
    cfg: _Config,
    client: httpx.AsyncClient,
    *,
    clubgg_club: str,
    player_id: str,
    amount: Decimal,
    request_id: str,
    label: str,
    ptb_bot: Any | None,
) -> bool:
    """Submit one deposit, poll to terminal, notify. Returns True on success/dry_run/skipped."""
    amount_str = _format_chip_amount(amount)
    logger.info(
        "auto_chip_add: submitting %s club=%s (id=%s) player=%s amount=%s dry_run=%s "
        "request_id=%s",
        label,
        clubgg_club,
        CLUBGG_CLUB_IDS.get(clubgg_club),
        player_id,
        amount_str,
        cfg.dry_run,
        request_id,
    )

    job_id, status, error = await _submit_deposit(
        cfg,
        client,
        club=clubgg_club,
        player_id=player_id,
        amount=amount_str,
        request_id=request_id,
    )
    if error or not job_id:
        await _send_alert(
            cfg,
            ptb_bot,
            f"Auto chip-add FAILED to queue ({label}) for {clubgg_club} player "
            f"{player_id} ({amount_str}): {error}. Add chips manually.",
        )
        return False

    if status and status.lower() in _TERMINAL_STATUSES:
        job = {"status": status, "job_id": job_id}
    else:
        job = await _poll_until_terminal(cfg, client, job_id)

    await _notify_result(
        cfg, ptb_bot, job, clubgg_club, player_id, amount_str, label=label
    )
    status = (job.get("status") or "unknown").lower()
    return status in ("success", "dry_run", "skipped")


async def trigger_auto_chip_add(
    *,
    club_id: int,
    chat_id: int,
    message_id: int,
    amount: Decimal,
    bonus: Optional[Decimal] = None,
    group_title: Optional[str] = None,
    ptb_bot: Any | None = None,
) -> None:
    """Fire-and-forget orchestration. Safe to schedule with create_task.

    Never raises; degrades to a no-op (plus best-effort staff alert) on any problem
    so the existing /add behaviour is never affected.
    """
    try:
        cfg = load_config()
        if cfg is None:
            return  # feature not configured on this worker

        enabled = await asyncio.to_thread(get_auto_chip_adding_enabled, int(club_id))
        if not enabled:
            return

        request_id = request_id_for(chat_id, message_id)
        if not _claim_request(request_id):
            return  # already handled by the other /add code path

        transactions = _deposit_transactions(amount, bonus)

        # Resolve player id from the group title.
        title = group_title
        if not title:
            title, _cid = await asyncio.to_thread(get_group_title_for_chat, int(chat_id))
        player_id = gg_player_id_from_title(title)
        if not player_id:
            await _send_alert(
                cfg,
                ptb_bot,
                f"Auto chip-add skipped (chat {chat_id}): could not read a player id "
                f"from the group title. Add chips manually.",
            )
            return

        club = await asyncio.to_thread(get_club_by_id, int(club_id))
        club_name = club.name if club else None

        # Round Table needs the customer's last RT/AT deposit choice.
        union_shorthand: Optional[str] = None
        if (club_name or "").strip().lower() == ROUND_TABLE_CLUB_NAME.strip().lower():
            union_shorthand, recorded_at = await asyncio.to_thread(
                get_last_deposit_union, int(chat_id)
            )
            if not union_shorthand or not _union_is_fresh(
                recorded_at, cfg.union_max_age_hours
            ):
                await _send_alert(
                    cfg,
                    ptb_bot,
                    f"Auto chip-add skipped for player {player_id} (chat {chat_id}): "
                    f"no fresh Round Table/Aces Table selection. Ask the customer to "
                    f"run /deposit and pick the club, or add chips manually.",
                )
                return

        clubgg_club = resolve_clubgg_club_name(club_name, union_shorthand)
        if not clubgg_club:
            await _send_alert(
                cfg,
                ptb_bot,
                f"Auto chip-add skipped for player {player_id} (chat {chat_id}): "
                f"could not map club {club_name!r}/union {union_shorthand!r} to a "
                f"ClubGG club. Add chips manually.",
            )
            return

            return

        timeout = httpx.Timeout(cfg.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout) as client:
            ok, detail = await _health_ok(cfg, client)
            if not ok:
                parts = " + ".join(
                    _format_chip_amount(amt) for _lbl, amt, _part in transactions
                )
                await _send_alert(
                    cfg,
                    ptb_bot,
                    f"Auto chip-add NOT sent for {clubgg_club} player {player_id} "
                    f"({parts} chips): {detail}. Add chips manually.",
                )
                return

            for idx, (label, chip_amount, part) in enumerate(transactions):
                if idx > 0:
                    # Deposit bot runs one job at a time; wait for the prior job to finish.
                    await asyncio.sleep(cfg.poll_interval_sec)
                ok = await _run_single_deposit(
                    cfg,
                    client,
                    clubgg_club=clubgg_club,
                    player_id=player_id,
                    amount=chip_amount,
                    request_id=request_id_for(chat_id, message_id, part=part),
                    label=label,
                    ptb_bot=ptb_bot,
                )
                if not ok and idx == 0 and len(transactions) > 1:
                    await _send_alert(
                        cfg,
                        ptb_bot,
                        f"Auto chip-add: deposit failed for {clubgg_club} player "
                        f"{player_id}; bonus ({_format_chip_amount(transactions[1][1])}) "
                        f"was not attempted. Add chips manually.",
                    )
                    return
    except Exception:
        logger.exception("auto_chip_add: unexpected error (chat_id=%s)", chat_id)


async def _notify_result(
    cfg: _Config,
    ptb_bot: Any | None,
    job: dict[str, Any],
    clubgg_club: str,
    player_id: str,
    amount_str: str,
    *,
    label: str = "deposit",
) -> None:
    status = (job.get("status") or "unknown").lower()
    reason = job.get("reason") or ""
    base = f"{clubgg_club} player {player_id} ({amount_str} chips, {label})"

    if status == "success":
        logger.info("auto_chip_add: SUCCESS %s", base)
        if cfg.alert_on_success:
            await _send_alert(cfg, ptb_bot, f"Auto chip-add SUCCESS: sent {base}.")
        return
    if status == "dry_run":
        if cfg.alert_on_success:
            await _send_alert(
                cfg, ptb_bot, f"Auto chip-add DRY-RUN ok (no chips sent): {base}."
            )
        return
    if status == "skipped":
        logger.info("auto_chip_add: skipped (already done) %s", base)
        return

    # Problem / non-terminal-in-time states → always alert.
    if status == "uncertain":
        await _send_alert(
            cfg,
            ptb_bot,
            f"⚠️ Auto chip-add UNCERTAIN for {base}: {reason}. DO NOT retry — "
            f"check ClubGG manually.",
        )
    elif status in _PROBLEM_STATUSES:
        await _send_alert(
            cfg,
            ptb_bot,
            f"❌ Auto chip-add {status.upper()} for {base}: {reason}. "
            f"Verify and add chips manually if needed.",
        )
    else:  # timeout / unknown
        await _send_alert(
            cfg,
            ptb_bot,
            f"⚠️ Auto chip-add result unknown for {base} (status={status}). "
            f"Check ClubGG manually.",
        )
