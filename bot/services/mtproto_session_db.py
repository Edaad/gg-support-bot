"""PostgreSQL-backed Telethon session strings shared between API/web and bot/worker."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from club_gc_settings import ClubGcConfig, get_tg_mtproto_credentials

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _db_sessions_enabled() -> bool:
    return os.getenv("GC_MTPROTO_DB_SESSIONS", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def resolve_repo_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (REPO_ROOT / p).resolve()


def make_disk_sqlite_session_client(cfg: ClubGcConfig) -> TelegramClient:
    """SQLite session path only (no Postgres row). Matches Telethon ``.session`` file layout."""

    api_id, api_hash = get_tg_mtproto_credentials()
    resolved = resolve_repo_path(cfg.mtproto_session)
    if resolved.suffix == ".session":
        stem = resolved.with_suffix("")
    else:
        stem = resolved

    stem.resolve().parent.mkdir(parents=True, exist_ok=True)

    session_arg = stem.as_posix()
    return TelegramClient(session_arg, api_id=api_id, api_hash=api_hash)


def export_authorization_string(client: TelegramClient) -> str:
    """Serialize session credentials to portable StringSession form."""
    src = client.session
    auth = getattr(src, "auth_key", None)
    if auth is None or not getattr(auth, "key", b""):
        return ""
    blob = StringSession()
    blob.set_dc(src.dc_id, src.server_address, src.port)
    blob.auth_key = src.auth_key
    encoded = blob.save()
    return encoded if encoded else ""


def load_session_string_for_club(club_key: str) -> str | None:
    """Return stored StringSession blob for ``club_key``, or ``None`` if unset / disabled / error."""
    if not _db_sessions_enabled():
        return None
    try:
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from db.connection import get_db
        from db.models import MtProtoSessionCredential

        with get_db() as db:
            row = (
                db.query(MtProtoSessionCredential)
                .filter(MtProtoSessionCredential.club_key == club_key)
                .one_or_none()
            )
            if row is None or not (row.telethon_auth_string or "").strip():
                return None
            return row.telethon_auth_string.strip()
    except (OperationalError, ProgrammingError) as e:
        logger.warning("mtproto session DB read failed (using file fallback): %s", e)
        return None


def persist_session_string_for_club(club_key: str, telethon_auth_string: str) -> None:
    """Upsert StringSession blob (authorized session only)."""
    if not _db_sessions_enabled():
        return
    from db.connection import get_db
    from db.models import MtProtoSessionCredential

    s = telethon_auth_string.strip()
    if not s:
        return

    with get_db() as db:
        row = (
            db.query(MtProtoSessionCredential)
            .filter(MtProtoSessionCredential.club_key == club_key)
            .one_or_none()
        )
        if row is None:
            db.add(MtProtoSessionCredential(club_key=club_key, telethon_auth_string=s))
        else:
            row.telethon_auth_string = s


async def snapshot_disk_session_to_database(cfg: ClubGcConfig) -> bool:
    """Read SQLite ``.session`` from disk and persist StringSession blob to Postgres.

    Run on the Dashboard API host immediately after Telethon completes login so the bot
    worker (which lacks the web dyno’s ephemeral ``sessions/`` files) loads auth from Postgres.
    """
    if not _db_sessions_enabled():
        return False

    client = make_disk_sqlite_session_client(cfg)

    await client.connect()
    try:
        if not await client.is_user_authorized():

            return False
        blob = export_authorization_string(client)
    finally:
        await client.disconnect()

    if not blob:

        return False

    await asyncio.to_thread(persist_session_string_for_club, cfg.club_key, blob)

    logger.info("mtproto session snapshot saved club=%s", cfg.club_key)

    return True
