"""Worker-reported MTProto session health (read by Dashboard without live Telethon connect)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

STATUS_CONNECTED = "connected"
STATUS_DISCONNECTED = "disconnected"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_AUTH_KEY_DUPLICATED = "auth_key_duplicated"
STATUS_ERROR = "error"
STATUS_MTPROTO_DISABLED = "mtproto_disabled"
STATUS_UNKNOWN = "unknown"
STATUS_NO_SESSION = "no_session"


@dataclass(frozen=True)
class ClubHealthSnapshot:
    club_key: str
    worker_connected: bool
    session_valid: bool
    status: str
    status_detail: str | None
    telegram_user_id: int | None
    checked_at: datetime | None


def classify_mtproto_error(exc: BaseException) -> tuple[str, str]:
    """Map Telethon / connection errors to dashboard status codes."""

    name = type(exc).__name__
    msg = str(exc) or name
    if "AuthKeyDuplicated" in name or "AuthKeyDuplicated" in msg:
        return (
            STATUS_AUTH_KEY_DUPLICATED,
            "Telegram invalidated this session because the same auth key connected "
            "from another IP (AuthKeyDuplicated). Log in again here.",
        )
    if "AuthKeyUnregistered" in name or "AuthKeyUnregistered" in msg:
        return (
            STATUS_UNAUTHORIZED,
            "Telegram no longer recognizes this session. Log in again.",
        )
    if "SessionRevoked" in name or "SessionRevoked" in msg:
        return (
            STATUS_UNAUTHORIZED,
            "This Telegram session was revoked. Log in again.",
        )
    return STATUS_ERROR, msg


def persist_club_health(
    club_key: str,
    *,
    worker_connected: bool,
    session_valid: bool,
    status: str,
    status_detail: str | None = None,
    telegram_user_id: int | None = None,
) -> None:
    """Upsert worker health for ``club_key`` (sync; safe from listener thread via to_thread)."""

    try:
        from db.connection import get_db
        from db.models import MtProtoClubHealth

        now = datetime.now(timezone.utc)
        with get_db() as db:
            row = db.get(MtProtoClubHealth, club_key)
            if row is None:
                row = MtProtoClubHealth(club_key=club_key)
                db.add(row)
            row.worker_connected = bool(worker_connected)
            row.session_valid = bool(session_valid)
            row.status = status
            row.status_detail = (status_detail or "").strip() or None
            row.telegram_user_id = telegram_user_id
            row.checked_at = now
    except Exception:
        logger.warning("mtproto club health persist failed club=%s", club_key, exc_info=True)


def load_club_health(club_key: str) -> ClubHealthSnapshot | None:
    try:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from db.connection import get_db
        from db.models import MtProtoClubHealth

        with get_db() as db:
            row = db.get(MtProtoClubHealth, club_key)
            if row is None:
                return None
            checked = row.checked_at
            if checked is not None and checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            return ClubHealthSnapshot(
                club_key=str(row.club_key),
                worker_connected=bool(row.worker_connected),
                session_valid=bool(row.session_valid),
                status=str(row.status or STATUS_UNKNOWN),
                status_detail=row.status_detail,
                telegram_user_id=row.telegram_user_id,
                checked_at=checked,
            )
    except (OperationalError, ProgrammingError) as e:
        logger.warning("mtproto club health read failed: %s", e)
        return None
    except Exception:
        logger.warning("mtproto club health read failed club=%s", club_key, exc_info=True)
        return None


def resolve_auxiliary_session_status(
    *,
    session_stored: bool,
    session_role: str,
) -> dict[str, Any]:
    """Dashboard status for creator/link-join sessions (no worker listener)."""

    role_detail = {
        "creator": "Group creator only — used briefly when /gc creates a new megagroup.",
        "link_join": "Link-join account — joins new groups via invite link during /gc.",
    }.get(session_role, "Auxiliary session — not used by the DM listener.")

    if not session_stored:
        return {
            "session_stored": False,
            "session_authorized": False,
            "worker_status": STATUS_NO_SESSION,
            "worker_status_detail": None,
            "worker_checked_at": None,
        }

    return {
        "session_stored": True,
        "session_authorized": True,
        "worker_status": "auxiliary",
        "worker_status_detail": role_detail,
        "worker_checked_at": None,
    }


def resolve_club_session_status(
    club_key: str,
    *,
    session_stored: bool,
    mtproto_enabled: bool,
    listener_enabled: bool,
) -> dict[str, Any]:
    """Dashboard-facing status derived from Postgres session blob + worker health."""

    if not session_stored:
        return {
            "session_stored": False,
            "session_authorized": False,
            "worker_status": STATUS_NO_SESSION,
            "worker_status_detail": None,
            "worker_checked_at": None,
        }

    if not mtproto_enabled:
        return {
            "session_stored": True,
            "session_authorized": False,
            "worker_status": STATUS_MTPROTO_DISABLED,
            "worker_status_detail": (
                "GC_MTPROTO_ENABLED is off on the worker — Telethon is paused. "
                "Re-enable before the bot can use stored sessions."
            ),
            "worker_checked_at": None,
        }

    if not listener_enabled:
        return {
            "session_stored": True,
            "session_authorized": False,
            "worker_status": STATUS_MTPROTO_DISABLED,
            "worker_status_detail": (
                "GC_DM_GC_LISTENER_ENABLED is off — the worker is not running Telethon listeners."
            ),
            "worker_checked_at": None,
        }

    health = load_club_health(club_key)
    if health is None:
        return {
            "session_stored": True,
            "session_authorized": False,
            "worker_status": STATUS_UNKNOWN,
            "worker_status_detail": (
                "Session saved in Postgres, but the worker has not reported live status yet "
                "(recent deploy or listener still starting)."
            ),
            "worker_checked_at": None,
        }

    active = (
        health.status == STATUS_CONNECTED
        and health.session_valid
        and health.worker_connected
    )
    detail = health.status_detail
    if not active and not detail:
        if health.status == STATUS_DISCONNECTED:
            detail = "Worker is up but this club's Telethon client is not connected."
        elif health.status == STATUS_UNAUTHORIZED:
            detail = "Stored session is no longer authorized. Log in again."

    return {
        "session_stored": True,
        "session_authorized": active,
        "worker_status": health.status,
        "worker_status_detail": detail,
        "worker_checked_at": health.checked_at,
    }
