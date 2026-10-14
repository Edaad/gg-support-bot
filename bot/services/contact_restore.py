"""Worker job: put deleted Telegram contacts back, one AddContact per club per tick.

Uses the live dm_gc Telethon client. A second connection would drop the listener.
Default interval is 90 seconds. FloodWait pauses only that club.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon.errors import FloodWaitError, PeerFloodError
from telethon.tl.functions.contacts import AddContactRequest, GetContactsRequest
from telethon.tl.types import (
    Channel,
    Chat,
    InputPeerChannel,
    InputPeerChat,
    InputUser,
    User,
)

from club_gc_settings import (
    CONTACT_RESTORE_CLUB_KEYS,
    contact_restore_active_club_keys,
    get_contact_restore_interval_sec,
    is_dm_gc_listener_enabled,
)

logger = logging.getLogger(__name__)

_JOB_NAME = "contact_restore"
_APP = None

MIN_BOOT_DELAY_SEC = 60.0
FLOOD_EXTRA_SEC = 60
PEER_FLOOD_PAUSE_SEC = 3600
PARTICIPANT_LIMIT = 100
_NAME_MAX = 64
_CLUBS = frozenset(CONTACT_RESTORE_CLUB_KEYS)

_ROW_KEYS = (
    "club_key",
    "contact_user_id",
    "username",
    "first_name",
    "last_name",
    "status",
)


@dataclass(frozen=True)
class ClubSnapshot:
    dialogs_filled: bool
    flood_resume_at: datetime | None
    ready_row_id: int | None
    username_row_id: int | None
    unscanned_group_id: int | None
    pending_count: int
    total_count: int


def clip_name(raw: str, limit: int = _NAME_MAX) -> str:
    s = (raw or "").strip()
    if len(s) <= limit:
        return s
    if limit < 2:
        return s[:limit]
    return s[: limit - 1].rstrip() + "…"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def choose_action(snap: ClubSnapshot, now: datetime) -> str:
    """One step for this club. A tick never resolves and adds in the same call."""

    resume = snap.flood_resume_at
    if resume is not None and _aware(resume) > _aware(now):
        return "flood"
    if snap.total_count == 0:
        return "empty"
    if not snap.dialogs_filled:
        if snap.pending_count == 0:
            return "drained"
        return "dialogs"
    if snap.ready_row_id is not None:
        return "add"
    if snap.username_row_id is not None:
        return "username"
    if snap.unscanned_group_id is not None:
        return "group"
    return "drained"


def should_auto_disable(actions: list[str]) -> bool:
    return bool(actions) and all(action == "drained" for action in actions)


def flood_resume_deadline(now: datetime, seconds: int, *, peer: bool) -> datetime:
    if peer:
        return _aware(now) + timedelta(seconds=PEER_FLOOD_PAUSE_SEC)
    return _aware(now) + timedelta(seconds=int(seconds) + FLOOD_EXTRA_SEC)


def compute_contact_restore_first_delay_sec(
    *,
    last_tick: datetime | None,
    now: datetime,
    interval_sec: int,
) -> float:
    if last_tick is None:
        return MIN_BOOT_DELAY_SEC
    remaining = (
        _aware(last_tick) + timedelta(seconds=interval_sec) - _aware(now)
    ).total_seconds()
    if remaining <= 0:
        return MIN_BOOT_DELAY_SEC
    return remaining


def parse_contact_restore_csv(path: Path, *, club_key: str | None = None) -> list[dict]:
    """Read the export. Phone is never copied onto the returned rows."""

    found: dict[tuple[str, int], dict] = {}
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for raw in csv.DictReader(handle):
            club = (raw.get("club_key") or "").strip()
            if club not in _CLUBS:
                continue
            if club_key and club != club_key:
                continue
            try:
                user_id = int(raw["contact_user_id"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (club, user_id)
            if key in found:
                continue
            first = clip_name(raw.get("first_name") or "")
            username = (raw.get("username") or "").strip().lstrip("@")
            found[key] = {
                "club_key": club,
                "contact_user_id": user_id,
                "username": username or None,
                "first_name": first,
                "last_name": clip_name(raw.get("last_name") or ""),
                "status": "skipped" if not first else "pending",
            }
    return list(found.values())


def _user_access_hash(entity) -> int | None:
    if not isinstance(entity, User):
        return None
    access = getattr(entity, "access_hash", None)
    if not access:
        return None
    return int(access)


def _group_peer_fields(entity) -> tuple[str, int, int] | None:
    if isinstance(entity, Channel) and getattr(entity, "megagroup", False):
        if getattr(entity, "left", False):
            return None
        access = int(getattr(entity, "access_hash", 0) or 0)
        if not access:
            return None
        return ("channel", int(entity.id), access)
    if isinstance(entity, Chat):
        if getattr(entity, "left", False):
            return None
        return ("chat", int(entity.id), 0)
    return None


def _ensure_control(session):
    from db.models import ContactRestoreControl

    row = session.get(ContactRestoreControl, 1)
    if row is None:
        row = ContactRestoreControl(id=1)
        session.add(row)
        session.flush()
    return row


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(raw))
    except ValueError:
        return None


def load_snapshot(club: str) -> ClubSnapshot:
    from db.connection import get_db
    from db.models import ContactRestoreGroup, ContactRestoreRow

    with get_db() as session:
        control = _ensure_control(session)
        filled_map = control.dialogs_filled_at or {}
        flood_map = control.flood_resume_at or {}
        dialogs_filled = bool(filled_map.get(club))
        flood_at = _parse_dt(flood_map.get(club))
        pending_count = (
            session.query(ContactRestoreRow)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
            )
            .count()
        )
        total_count = (
            session.query(ContactRestoreRow)
            .filter(ContactRestoreRow.club_key == club)
            .count()
        )
        ready_row_id = (
            session.query(ContactRestoreRow.id)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
                ContactRestoreRow.access_hash.isnot(None),
                ContactRestoreRow.last_error.is_(None),
            )
            .order_by(ContactRestoreRow.id)
            .limit(1)
            .scalar()
        )
        username_row_id = (
            session.query(ContactRestoreRow.id)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
                ContactRestoreRow.access_hash.is_(None),
                ContactRestoreRow.username.isnot(None),
                ContactRestoreRow.username != "",
                ContactRestoreRow.username_miss.is_(False),
            )
            .order_by(ContactRestoreRow.id)
            .limit(1)
            .scalar()
        )
        unscanned_group_id = (
            session.query(ContactRestoreGroup.id)
            .filter(
                ContactRestoreGroup.club_key == club,
                ContactRestoreGroup.scanned_at.is_(None),
            )
            .order_by(ContactRestoreGroup.id)
            .limit(1)
            .scalar()
        )
    return ClubSnapshot(
        dialogs_filled=dialogs_filled,
        flood_resume_at=flood_at,
        ready_row_id=ready_row_id,
        username_row_id=username_row_id,
        unscanned_group_id=unscanned_group_id,
        pending_count=pending_count,
        total_count=total_count,
    )


def club_counts(club: str) -> tuple[int, int]:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    with get_db() as session:
        added = (
            session.query(ContactRestoreRow)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "added",
            )
            .count()
        )
        pending = (
            session.query(ContactRestoreRow)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
            )
            .count()
        )
    return added, pending


def _load_row(row_id: int) -> SimpleNamespace | None:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    with get_db() as session:
        row = session.get(ContactRestoreRow, row_id)
        if row is None:
            return None
        return SimpleNamespace(
            id=row.id,
            contact_user_id=int(row.contact_user_id),
            access_hash=None if row.access_hash is None else int(row.access_hash),
            first_name=row.first_name or "",
            last_name=row.last_name or "",
            username=row.username or "",
        )


def _load_group(group_id: int) -> SimpleNamespace | None:
    from db.connection import get_db
    from db.models import ContactRestoreGroup

    with get_db() as session:
        row = session.get(ContactRestoreGroup, group_id)
        if row is None:
            return None
        return SimpleNamespace(
            id=row.id,
            chat_id=int(row.chat_id),
            access_hash=int(row.access_hash or 0),
            peer_type=row.peer_type,
        )


def _set_status(row_id: int, status: str) -> None:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ContactRestoreRow, row_id)
        if row is None:
            return
        row.status = status
        row.updated_at = now
        if status == "added":
            row.last_error = None


def _set_last_error(row_id: int, message: str) -> None:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ContactRestoreRow, row_id)
        if row is None:
            return
        row.last_error = (message or "")[:500]
        row.updated_at = now


def _mark_username_miss(row_id: int, reason: str) -> None:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ContactRestoreRow, row_id)
        if row is None:
            return
        row.username_miss = True
        row.last_error = (reason or "")[:500]
        row.updated_at = now


def _save_access_hash(row_id: int, access_hash: int, via: str) -> None:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ContactRestoreRow, row_id)
        if row is None or row.access_hash is not None:
            return
        row.access_hash = int(access_hash)
        row.resolve_via = via
        row.updated_at = now


def _set_flood_resume(club: str, resume_at: datetime) -> None:
    from db.connection import get_db

    with get_db() as session:
        row = _ensure_control(session)
        current = dict(row.flood_resume_at or {})
        current[club] = _aware(resume_at).isoformat()
        row.flood_resume_at = current


def _mark_dialogs_filled(club: str) -> None:
    from db.connection import get_db

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = _ensure_control(session)
        current = dict(row.dialogs_filled_at or {})
        current[club] = now.isoformat()
        row.dialogs_filled_at = current


def _pending_user_ids(club: str) -> set[int]:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    with get_db() as session:
        rows = (
            session.query(ContactRestoreRow.contact_user_id)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
            )
            .all()
        )
    return {int(user_id) for (user_id,) in rows}


def _mark_added_ids(club: str, user_ids: set[int]) -> None:
    if not user_ids:
        return
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        rows = (
            session.query(ContactRestoreRow)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
                ContactRestoreRow.contact_user_id.in_(list(user_ids)),
            )
            .all()
        )
        for row in rows:
            row.status = "added"
            row.updated_at = now


def _save_groups(club: str, groups: list[tuple[str, int, int]]) -> None:
    if not groups:
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db.connection import get_db
    from db.models import ContactRestoreGroup

    deduped: dict[int, tuple[str, int, int]] = {}
    for peer in groups:
        deduped[peer[1]] = peer
    values = [
        {
            "club_key": club,
            "chat_id": chat_id,
            "access_hash": access,
            "peer_type": peer_type,
        }
        for peer_type, chat_id, access in deduped.values()
    ]
    with get_db() as session:
        for offset in range(0, len(values), 500):
            stmt = pg_insert(ContactRestoreGroup).values(values[offset : offset + 500])
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_contact_restore_groups_club_chat"
            )
            session.execute(stmt)


def _pending_without_hash(club: str) -> set[int]:
    from db.connection import get_db
    from db.models import ContactRestoreRow

    with get_db() as session:
        rows = (
            session.query(ContactRestoreRow.contact_user_id)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
                ContactRestoreRow.access_hash.is_(None),
            )
            .all()
        )
    return {int(user_id) for (user_id,) in rows}


def _mark_group_scanned(group_id: int) -> None:
    from db.connection import get_db
    from db.models import ContactRestoreGroup

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ContactRestoreGroup, group_id)
        if row is None:
            return
        row.scanned_at = now


def _save_hashes_via(club: str, found: dict[int, int], via: str) -> None:
    if not found:
        return
    from db.connection import get_db
    from db.models import ContactRestoreRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        rows = (
            session.query(ContactRestoreRow)
            .filter(
                ContactRestoreRow.club_key == club,
                ContactRestoreRow.status == "pending",
                ContactRestoreRow.access_hash.is_(None),
                ContactRestoreRow.contact_user_id.in_(list(found)),
            )
            .all()
        )
        for row in rows:
            access = found.get(int(row.contact_user_id))
            if not access:
                continue
            row.access_hash = access
            row.resolve_via = via
            row.updated_at = now


async def fill_dialogs(club: str, client) -> None:
    pending_ids = _pending_user_ids(club)
    book = await client(GetContactsRequest(hash=0))
    already = {
        int(user.id)
        for user in (getattr(book, "users", None) or [])
        if getattr(user, "id", None)
    }
    _mark_added_ids(club, pending_ids & already)
    found: dict[int, int] = {}
    groups: list[tuple[str, int, int]] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, User):
            user_id = int(entity.id)
            access = _user_access_hash(entity)
            if user_id in pending_ids and user_id not in already and access:
                found[user_id] = access
            continue
        peer = _group_peer_fields(entity)
        if peer is not None:
            groups.append(peer)
    _save_hashes_via(club, found, "dm")
    _save_groups(club, groups)
    _mark_dialogs_filled(club)


async def _add_loaded(club: str, client, row_id: int | None) -> None:
    if row_id is None:
        return
    row = _load_row(row_id)
    if row is None or row.access_hash is None:
        return
    first = clip_name(row.first_name)
    if not first:
        _set_status(row.id, "skipped")
        return
    try:
        await client(
            AddContactRequest(
                id=InputUser(int(row.contact_user_id), int(row.access_hash)),
                first_name=first,
                last_name=clip_name(row.last_name),
                phone="",
                add_phone_privacy_exception=False,
            )
        )
    except (FloodWaitError, PeerFloodError):
        raise
    except Exception as exc:
        logger.warning(
            "contact_restore add failed club=%s row_id=%s %s",
            club,
            row_id,
            type(exc).__name__,
        )
        _set_last_error(row_id, type(exc).__name__)
        return
    _set_status(row.id, "added")


async def _resolve_loaded(client, row_id: int | None) -> None:
    if row_id is None:
        return
    row = _load_row(row_id)
    if row is None or not row.username:
        _mark_username_miss(row_id or 0, "no_username")
        return
    try:
        entity = await client.get_entity(row.username)
    except (FloodWaitError, PeerFloodError):
        raise
    except Exception as exc:
        _mark_username_miss(row_id, type(exc).__name__)
        return
    if int(getattr(entity, "id", 0) or 0) != row.contact_user_id:
        _mark_username_miss(row_id, "id_mismatch")
        return
    access = _user_access_hash(entity)
    if access is None:
        _mark_username_miss(row_id, "no_access_hash")
        return
    _save_access_hash(row_id, access, "username")


async def _scan_loaded(club: str, client, group_id: int | None) -> None:
    if group_id is None:
        return
    group = _load_group(group_id)
    if group is None:
        return
    if group.peer_type == "channel":
        peer = InputPeerChannel(group.chat_id, group.access_hash)
    else:
        peer = InputPeerChat(group.chat_id)
    try:
        participants = await client.get_participants(peer, limit=PARTICIPANT_LIMIT)
    except (FloodWaitError, PeerFloodError):
        raise
    except Exception as exc:
        logger.warning(
            "contact_restore group failed club=%s group_id=%s %s",
            club,
            group_id,
            type(exc).__name__,
        )
        _mark_group_scanned(group_id)
        return
    pending = _pending_without_hash(club)
    found: dict[int, int] = {}
    for user in participants:
        user_id = int(getattr(user, "id", 0) or 0)
        if user_id not in pending:
            continue
        access = _user_access_hash(user)
        if access:
            found[user_id] = access
    _save_hashes_via(club, found, "group")
    _mark_group_scanned(group_id)


def _log(club: str, action: str, *, wait_s: int | None = None) -> None:
    try:
        added, pending = club_counts(club)
    except Exception:
        logger.warning("contact_restore counts failed club=%s", club, exc_info=True)
        added, pending = -1, -1
    if wait_s is None:
        logger.info(
            "contact_restore club=%s action=%s added=%s pending=%s",
            club,
            action,
            added,
            pending,
        )
        return
    logger.info(
        "contact_restore club=%s action=%s wait_s=%s added=%s pending=%s",
        club,
        action,
        wait_s,
        added,
        pending,
    )


async def tick_club(club: str, client, now: datetime) -> str:
    snap = load_snapshot(club)
    action = choose_action(snap, now)
    wait_s: int | None = None
    if action in ("flood", "empty", "drained"):
        _log(club, action)
        return action
    try:
        if action == "dialogs":
            await fill_dialogs(club, client)
        elif action == "add":
            await _add_loaded(club, client, snap.ready_row_id)
        elif action == "username":
            await _resolve_loaded(client, snap.username_row_id)
        elif action == "group":
            await _scan_loaded(club, client, snap.unscanned_group_id)
    except FloodWaitError as exc:
        wait_s = int(exc.seconds)
        _set_flood_resume(club, flood_resume_deadline(now, wait_s, peer=False))
        action = "flood"
    except PeerFloodError:
        wait_s = PEER_FLOOD_PAUSE_SEC
        _set_flood_resume(club, flood_resume_deadline(now, 0, peer=True))
        action = "peer_flood"
    _log(club, action, wait_s=wait_s)
    return action


def is_contact_restore_auto_disabled() -> bool:
    try:
        from db.connection import get_db
        from db.models import ContactRestoreControl

        with get_db() as session:
            row = session.get(ContactRestoreControl, 1)
            return row is not None and row.auto_disabled_at is not None
    except Exception:
        logger.warning("contact_restore: auto-disable check failed", exc_info=True)
        return False


def _last_tick_at() -> datetime | None:
    from db.connection import get_db

    with get_db() as session:
        row = _ensure_control(session)
        if row.last_tick_at is None:
            return None
        return _aware(row.last_tick_at)


def record_contact_restore_tick() -> None:
    from db.connection import get_db

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = _ensure_control(session)
        row.last_tick_at = now


def mark_auto_disabled() -> None:
    from db.connection import get_db

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = _ensure_control(session)
        row.auto_disabled_at = now
        row.auto_disabled_reason = "all_clubs_drained"


def remove_contact_restore_job() -> None:
    if _APP is None or _APP.job_queue is None:
        return
    for job in _APP.job_queue.get_jobs_by_name(_JOB_NAME):
        job.schedule_removal()


async def _tick_club_locked(club: str, now: datetime) -> str:
    from bot.services.mtproto_dm_gc_listener import get_listener_client
    from bot.services.mtproto_group_create import get_mtproto_lock

    async with get_mtproto_lock(club):
        client = get_listener_client(club)
        if client is None or not client.is_connected():
            logger.warning("contact_restore club=%s action=no_client", club)
            return "no_client"
        return await tick_club(club, client, now)


async def tick_async() -> None:
    if is_contact_restore_auto_disabled():
        remove_contact_restore_job()
        return
    record_contact_restore_tick()
    now = datetime.now(timezone.utc)
    actions = []
    for club in contact_restore_active_club_keys():
        try:
            actions.append(await _tick_club_locked(club, now))
        except Exception:
            logger.warning("contact_restore tick failed club=%s", club, exc_info=True)
            actions.append("error")
    if should_auto_disable(actions):
        mark_auto_disabled()
        remove_contact_restore_job()
        logger.info("contact_restore auto-disabled reason=all_clubs_drained")


def schedule_contact_restore_tick() -> None:
    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        logger.warning("contact_restore: listener loop not running; skipping tick")
        return
    asyncio.run_coroutine_threadsafe(tick_async(), loop)


def contact_restore_job_callback(context) -> None:
    schedule_contact_restore_tick()


def setup_contact_restore_job(app) -> None:
    """Schedule the restore cron after the listener is up. Default off."""

    global _APP

    if not is_dm_gc_listener_enabled():
        return
    from club_gc_settings import is_contact_restore_enabled

    if not is_contact_restore_enabled():
        return
    if app.job_queue is None:
        logger.warning("contact_restore: job_queue unavailable; job not scheduled")
        return

    _APP = app
    remove_contact_restore_job()
    interval_sec = get_contact_restore_interval_sec()
    try:
        last_tick = _last_tick_at()
    except Exception:
        logger.warning("contact_restore: last tick unreadable", exc_info=True)
        last_tick = None
    first_delay_sec = compute_contact_restore_first_delay_sec(
        last_tick=last_tick,
        now=datetime.now(timezone.utc),
        interval_sec=interval_sec,
    )
    app.job_queue.run_repeating(
        contact_restore_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name=_JOB_NAME,
    )
    logger.info(
        "contact_restore job scheduled first_delay_sec=%s interval_sec=%s clubs=%s",
        first_delay_sec,
        interval_sec,
        ",".join(contact_restore_active_club_keys()),
    )
