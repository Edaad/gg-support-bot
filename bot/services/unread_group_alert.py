"""Live unread badges for support groups, and a Pushover when 5 or more are unread.

The DM/GC listener's club accounts are the source of truth. This module snapshots
their dialogs once per connection, then applies incoming messages and inbox-read
updates. The combined count is across Round Table, Creator Club, and ClubGTO.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import GetPeerDialogsRequest
from telethon.tl.types import (
    InputDialogPeer,
    PeerChannel,
    UpdateDialogUnreadMark,
    UpdateReadChannelInbox,
    UpdateReadHistoryInbox,
)
from telethon.utils import get_peer_id

from club_gc_settings import CLUB_GC_CONFIG, ClubGcConfig

logger = logging.getLogger(__name__)

THRESHOLD = 5
REPEAT_AFTER = timedelta(minutes=5)
FAILED_RETRY_SEC = 30.0
PUSHOVER_MAX_LEN = 1024
PUSHOVER_TITLE = "Unread group chats"
PUSHOVER_SOURCE = "unread_group_alert"

_clients: dict[str, TelegramClient] = {}
_dialogs: dict[str, dict[int, "LiveDialog"]] = {}
_absent: dict[str, set[int]] = {}
_ready: set[str] = set()
_snapshot_tasks: list[asyncio.Task[None]] = []
_timer: asyncio.Task[None] | None = None
_eval_task: asyncio.Task[None] | None = None
_eval_again = False
_stopped = False
_lock: asyncio.Lock | None = None
_ignored: dict[str, set[int]] = {}
_unknown_inflight: set[tuple[str, int]] = set()
_unknown_tasks: set[asyncio.Task[None]] = set()
DISCOVERY_SEC = 60.0


@dataclass(frozen=True)
class GroupRow:
    chat_id: int
    club_key: str
    club_display_name: str
    name: str | None
    is_internal: bool


@dataclass(frozen=True)
class DialogSnap:
    unread_count: int
    title: str
    archived: bool


@dataclass
class LiveDialog:
    unread_count: int
    title: str
    archived: bool
    top_message_id: int

    def snap(self) -> DialogSnap:
        return DialogSnap(
            unread_count=self.unread_count,
            title=self.title,
            archived=self.archived,
        )


def _lock_for() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ready_to_evaluate(connected: set[str], ready: set[str]) -> bool:
    """True once every club connected in this cycle has finished its snapshot."""

    return bool(connected) and connected <= ready


def alert_action(count: int, last_sent_at: datetime | None, now: datetime) -> str:
    """``clear`` below 5, ``send`` when due, ``wait`` inside the 5-minute gap."""

    if count < THRESHOLD:
        return "clear"
    if last_sent_at is None:
        return "send"
    if as_utc(now) - as_utc(last_sent_at) >= REPEAT_AFTER:
        return "send"
    return "wait"


def display_title(telegram_title: str | None, group_name: str | None) -> str:
    telegram = (telegram_title or "").strip()
    if telegram:
        return telegram
    name = (group_name or "").strip()
    return name or "(untitled)"


def visible_unreads(
    groups: list[GroupRow],
    dialogs: dict[tuple[str, int], DialogSnap | None],
    ready_clubs: set[str],
) -> list[tuple[str, str]]:
    """``(club display name, title)`` for chats that count, sorted for a stable push."""

    rows: list[tuple[str, str]] = []
    for group in groups:
        if group.club_key not in ready_clubs or group.is_internal:
            continue
        snap = dialogs.get((group.club_key, group.chat_id))
        if snap is None or snap.unread_count < 1:
            continue
        rows.append((group.club_display_name, display_title(snap.title, group.name)))
    rows.sort(key=lambda row: (row[0].casefold(), row[1].casefold()))
    return rows


def format_unread_alert(count: int, titles: list[str]) -> str:
    """Count sentence first. Title lines fill whatever Pushover length remains."""

    header = (
        f"There are {count} unread group chats. Admin might not be active right now."
    )
    if len(header) > PUSHOVER_MAX_LEN:
        return header[: PUSHOVER_MAX_LEN - 1] + "…"
    body = header
    for title in titles:
        line = title.strip() or "(untitled)"
        candidate = body + "\n" + line
        if len(candidate) > PUSHOVER_MAX_LEN:
            break
        body = candidate
    return body


def begin_cycle() -> None:
    """Drop the previous cycle's mirror before new clients attach."""

    global _stopped
    _stopped = False
    _clients.clear()
    _dialogs.clear()
    _absent.clear()
    _ready.clear()
    _ignored.clear()
    _unknown_inflight.clear()
    _unknown_tasks.clear()


async def end_cycle() -> None:
    """Cancel snapshot work and forget in-memory badges. The send time stays in Postgres."""

    global _stopped, _eval_again
    _stopped = True
    _eval_again = False
    tasks = list(_snapshot_tasks) + list(_unknown_tasks)
    _snapshot_tasks.clear()
    _unknown_tasks.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("unread_group_alert: snapshot task failed during shutdown")
    _cancel_timer()
    if _eval_task is not None and not _eval_task.done():
        _eval_task.cancel()
        try:
            await _eval_task
        except asyncio.CancelledError:
            pass
    async with _lock_for():
        _clients.clear()
        _dialogs.clear()
        _absent.clear()
        _ready.clear()
        _ignored.clear()
        _unknown_inflight.clear()


async def supervise() -> None:
    """Recheck linked groups until the listener cycle cancels this task.

    A group row added after the snapshot has no dialog entry yet. This pass
    picks it up without walking every dialog again.
    """

    while True:
        await asyncio.sleep(DISCOVERY_SEC)
        schedule_evaluate()


def attach_client(client: TelegramClient, cfg: ClubGcConfig) -> None:
    """Register update handlers and start this club's dialog snapshot."""

    club_key = cfg.club_key
    _clients[club_key] = client

    async def _on_message(event: events.NewMessage.Event) -> None:
        await _handle_incoming(club_key, event)

    async def _on_raw(update: Any) -> None:
        await _handle_raw(club_key, update)

    client.add_event_handler(
        _on_message,
        events.NewMessage(incoming=True, func=lambda event: not event.is_private),
    )
    client.add_event_handler(_on_raw, events.Raw)
    task = asyncio.create_task(
        _snapshot_with_retry(client, cfg),
        name=f"unread-snapshot-{club_key}",
    )
    _snapshot_tasks.append(task)


async def _snapshot_with_retry(client: TelegramClient, cfg: ClubGcConfig) -> None:
    delay = 5.0
    while not _stopped and client.is_connected():
        try:
            await _snapshot_once(client, cfg)
            return
        except asyncio.CancelledError:
            raise
        except FloodWaitError as exc:
            wait = float(exc.seconds) + 1.0
            logger.warning(
                "unread_group_alert: snapshot flood wait club=%s seconds=%s",
                cfg.club_key,
                exc.seconds,
            )
            await asyncio.sleep(wait)
        except Exception:
            logger.exception(
                "unread_group_alert: snapshot failed club=%s", cfg.club_key
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def _snapshot_once(client: TelegramClient, cfg: ClubGcConfig) -> None:
    tracked = await asyncio.to_thread(load_tracked_groups)
    wanted = {
        chat_id
        for chat_id, group in tracked.get(cfg.club_key, {}).items()
        if not group.is_internal
    }
    found = await _collect_dialogs(client, wanted)
    async with _lock_for():
        if _stopped:
            return
        _dialogs[cfg.club_key] = found
        _absent[cfg.club_key] = wanted - set(found)
        _ready.add(cfg.club_key)
    logger.info(
        "unread_group_alert: snapshot club=%s dialogs=%s absent=%s",
        cfg.club_key,
        len(found),
        len(wanted - set(found)),
    )
    schedule_evaluate()


async def _collect_dialogs(
    client: TelegramClient, wanted: set[int]
) -> dict[int, LiveDialog]:
    found: dict[int, LiveDialog] = {}
    if not wanted:
        return found
    for archived in (False, True):
        async for dialog in client.iter_dialogs(archived=archived):
            if not dialog.is_group or dialog.id not in wanted:
                continue
            top = int(getattr(dialog.dialog, "top_message", 0) or 0)
            found[int(dialog.id)] = LiveDialog(
                unread_count=int(dialog.unread_count or 0),
                title=(dialog.title or "").strip(),
                archived=bool(dialog.archived),
                top_message_id=top,
            )
    return found


def _schedule_unknown(club_key: str, chat_id: int, message_id: int, title: str) -> None:
    """Caller holds ``_lock_for``. Look up a chat the snapshot did not see."""

    if chat_id in _ignored.get(club_key, set()):
        return
    key = (club_key, chat_id)
    if key in _unknown_inflight:
        return
    _unknown_inflight.add(key)
    task = asyncio.create_task(
        _resolve_unknown(club_key, chat_id, message_id, title),
        name=f"unread-unknown-{club_key}-{chat_id}",
    )
    _unknown_tasks.add(task)
    task.add_done_callback(_unknown_tasks.discard)


async def _resolve_unknown(
    club_key: str, chat_id: int, message_id: int, title: str
) -> None:
    try:
        tracked = await asyncio.to_thread(load_tracked_groups)
        if _stopped:
            return
        group = tracked.get(club_key, {}).get(chat_id)
        if group is None or group.is_internal:
            async with _lock_for():
                _ignored.setdefault(club_key, set()).add(chat_id)
            return
        client = _clients.get(club_key)
        if client is None or not client.is_connected():
            return
        await _fetch_one(client, club_key, chat_id)
        async with _lock_for():
            live = _dialogs.get(club_key, {}).get(chat_id)
            if live is not None and message_id > live.top_message_id:
                live.unread_count += 1
                live.top_message_id = message_id
                if title:
                    live.title = title
        schedule_evaluate()
    except FloodWaitError as exc:
        logger.warning(
            "unread_group_alert: unknown peer flood wait club=%s seconds=%s",
            club_key,
            exc.seconds,
        )
        _schedule_timer(float(exc.seconds) + 1.0, replace=True)
    except Exception:
        logger.exception(
            "unread_group_alert: unknown peer failed club=%s chat_id=%s",
            club_key,
            chat_id,
        )
    finally:
        async with _lock_for():
            _unknown_inflight.discard((club_key, chat_id))


async def _handle_incoming(club_key: str, event: events.NewMessage.Event) -> None:
    if _stopped or club_key not in _ready:
        return
    chat_id = event.chat_id
    message_id = getattr(event, "id", None)
    if chat_id is None or message_id is None:
        return
    title = ""
    chat = getattr(event, "chat", None)
    if chat is not None:
        title = (getattr(chat, "title", None) or "").strip()
    changed = False
    async with _lock_for():
        if _stopped or club_key not in _ready:
            return
        dialogs = _dialogs.setdefault(club_key, {})
        current = dialogs.get(int(chat_id))
        if current is None:
            if int(chat_id) not in _absent.get(club_key, set()):
                _schedule_unknown(club_key, int(chat_id), int(message_id), title)
                return
            _absent.get(club_key, set()).discard(int(chat_id))
            dialogs[int(chat_id)] = LiveDialog(
                unread_count=1,
                title=title,
                archived=False,
                top_message_id=int(message_id),
            )
            changed = True
        elif int(message_id) > current.top_message_id:
            current.unread_count += 1
            current.top_message_id = int(message_id)
            if title:
                current.title = title
            changed = True
    if changed:
        schedule_evaluate()


async def _handle_raw(club_key: str, update: Any) -> None:
    if _stopped or club_key not in _ready:
        return
    if isinstance(update, UpdateDialogUnreadMark):
        return
    chat_id: int | None = None
    still: int | None = None
    if isinstance(update, UpdateReadChannelInbox):
        chat_id = int(get_peer_id(PeerChannel(update.channel_id)))
        still = int(update.still_unread_count)
    elif isinstance(update, UpdateReadHistoryInbox):
        chat_id = int(get_peer_id(update.peer))
        still = int(update.still_unread_count)
    if chat_id is None or still is None:
        return
    changed = False
    async with _lock_for():
        if _stopped or club_key not in _ready:
            return
        current = _dialogs.get(club_key, {}).get(chat_id)
        if current is None:
            return
        count = max(0, still)
        if current.unread_count != count:
            current.unread_count = count
            changed = True
    if changed:
        schedule_evaluate()


def schedule_evaluate() -> None:
    global _eval_again, _eval_task
    if _stopped:
        return
    if _eval_task is not None and not _eval_task.done():
        _eval_again = True
        return
    _eval_task = asyncio.create_task(_evaluate_loop(), name="unread-group-alert-eval")


async def _evaluate_loop() -> None:
    global _eval_again
    try:
        while not _stopped:
            _eval_again = False
            await _evaluate()
            if not _eval_again or _stopped:
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("unread_group_alert: evaluate failed")


def _connected_and_ready() -> tuple[set[str], set[str]]:
    connected = {
        club_key for club_key, client in _clients.items() if client.is_connected()
    }
    ready = {club_key for club_key in connected if club_key in _ready}
    return connected, ready


async def _evaluate() -> None:
    async with _lock_for():
        connected, ready = _connected_and_ready()
    if not ready_to_evaluate(connected, ready):
        return
    tracked = await asyncio.to_thread(load_tracked_groups)
    try:
        await _fill_missing(tracked, connected)
    except FloodWaitError as exc:
        logger.warning("unread_group_alert: fill flood wait seconds=%s", exc.seconds)
        _schedule_timer(float(exc.seconds) + 1.0, replace=True)
        return
    async with _lock_for():
        connected, ready = _connected_and_ready()
        if not ready_to_evaluate(connected, ready):
            return
        rows = visible_unreads(
            _group_list(tracked),
            _dialog_index(),
            ready,
        )
    count = len(rows)
    now = datetime.now(timezone.utc)
    last_sent = await asyncio.to_thread(load_last_sent_at)
    action = alert_action(count, last_sent, now)
    if action == "clear":
        _cancel_timer()
        if last_sent is not None:
            await asyncio.to_thread(set_last_sent_at, None)
        return
    if action == "wait":
        assert last_sent is not None
        remaining = REPEAT_AFTER - (now - as_utc(last_sent))
        _ensure_timer(max(remaining.total_seconds(), 1.0))
        return
    message = format_unread_alert(count, [title for _club, title in rows])
    if not await _fanout(message):
        logger.warning("unread_group_alert: pushover failed count=%s", count)
        _schedule_timer(FAILED_RETRY_SEC, replace=True)
        return
    await asyncio.to_thread(set_last_sent_at, now)
    logger.info("unread_group_alert: sent count=%s", count)
    _schedule_timer(REPEAT_AFTER.total_seconds(), replace=True)


def _group_list(tracked: dict[str, dict[int, GroupRow]]) -> list[GroupRow]:
    return [group for groups in tracked.values() for group in groups.values()]


def _dialog_index() -> dict[tuple[str, int], DialogSnap | None]:
    index: dict[tuple[str, int], DialogSnap | None] = {}
    for club_key, dialogs in _dialogs.items():
        for chat_id, live in dialogs.items():
            index[(club_key, chat_id)] = live.snap()
    for club_key, chat_ids in _absent.items():
        for chat_id in chat_ids:
            index[(club_key, chat_id)] = None
    return index


async def _fill_missing(
    tracked: dict[str, dict[int, GroupRow]],
    connected: set[str],
) -> None:
    for club_key, groups in tracked.items():
        if club_key not in connected or club_key not in _ready:
            continue
        client = _clients.get(club_key)
        if client is None or not client.is_connected():
            continue
        async with _lock_for():
            known = set(_dialogs.get(club_key, {})) | set(_absent.get(club_key, set()))
            missing = [
                group.chat_id
                for group in groups.values()
                if not group.is_internal and group.chat_id not in known
            ]
        for chat_id in missing:
            if _stopped or not client.is_connected():
                return
            await _fetch_one(client, club_key, chat_id)


async def _fetch_one(client: TelegramClient, club_key: str, chat_id: int) -> None:
    try:
        input_peer = await client.get_input_entity(chat_id)
        result = await client(
            GetPeerDialogsRequest(peers=[InputDialogPeer(input_peer)])
        )
    except FloodWaitError:
        raise
    except Exception:
        logger.info(
            "unread_group_alert: peer unavailable club=%s chat_id=%s",
            club_key,
            chat_id,
        )
        async with _lock_for():
            if _stopped:
                return
            _absent.setdefault(club_key, set()).add(chat_id)
        return
    if not result.dialogs:
        async with _lock_for():
            if _stopped:
                return
            _absent.setdefault(club_key, set()).add(chat_id)
        return
    dialog = result.dialogs[0]
    title = _title_from_chats(result.chats, chat_id)
    async with _lock_for():
        if _stopped:
            return
        _absent.get(club_key, set()).discard(chat_id)
        _dialogs.setdefault(club_key, {})[chat_id] = LiveDialog(
            unread_count=int(dialog.unread_count or 0),
            title=title,
            archived=dialog.folder_id is not None,
            top_message_id=int(dialog.top_message or 0),
        )


def _title_from_chats(chats: list[Any], chat_id: int) -> str:
    for chat in chats:
        try:
            peer_id = int(get_peer_id(chat))
        except Exception:
            continue
        if peer_id == chat_id:
            return (getattr(chat, "title", None) or "").strip()
    return ""


def _cancel_timer() -> None:
    global _timer
    if _timer is not None and not _timer.done():
        _timer.cancel()
    _timer = None


def _ensure_timer(delay: float) -> None:
    global _timer
    if _stopped:
        return
    if _timer is not None and not _timer.done():
        return
    _timer = asyncio.create_task(_timer_fire(delay), name="unread-group-alert-timer")


def _schedule_timer(delay: float, *, replace: bool) -> None:
    global _timer
    if _stopped:
        return
    if replace:
        _cancel_timer()
    elif _timer is not None and not _timer.done():
        return
    _timer = asyncio.create_task(_timer_fire(delay), name="unread-group-alert-timer")


async def _timer_fire(delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    schedule_evaluate()


def load_tracked_groups() -> dict[str, dict[int, GroupRow]]:
    """Support-group rows for listener clubs, including the internal flag."""

    from db.connection import get_db
    from db.models import Group, SupportGroupChat

    by_club_id = {int(cfg.link_club_id): cfg for cfg in CLUB_GC_CONFIG.values()}
    if not by_club_id:
        return {}
    with get_db() as session:
        internal = {
            int(chat_id)
            for (chat_id,) in session.query(SupportGroupChat.telegram_chat_id)
            .filter(SupportGroupChat.is_internal.is_(True))
            .all()
        }
        rows = (
            session.query(Group.chat_id, Group.club_id, Group.name)
            .filter(Group.club_id.in_(list(by_club_id)))
            .all()
        )
    out: dict[str, dict[int, GroupRow]] = {}
    for chat_id, club_id, name in rows:
        cfg = by_club_id.get(int(club_id))
        if cfg is None:
            continue
        group = GroupRow(
            chat_id=int(chat_id),
            club_key=cfg.club_key,
            club_display_name=cfg.club_display_name,
            name=name,
            is_internal=int(chat_id) in internal,
        )
        out.setdefault(cfg.club_key, {})[group.chat_id] = group
    return out


def load_last_sent_at() -> datetime | None:
    from db.connection import get_db
    from db.models import UnreadGroupAlertControl

    with get_db() as session:
        row = session.get(UnreadGroupAlertControl, 1)
        if row is None or row.last_sent_at is None:
            return None
        return as_utc(row.last_sent_at)


def set_last_sent_at(value: datetime | None) -> None:
    from db.connection import get_db
    from db.models import UnreadGroupAlertControl

    with get_db() as session:
        row = session.get(UnreadGroupAlertControl, 1)
        if row is None:
            row = UnreadGroupAlertControl(id=1)
            session.add(row)
        row.last_sent_at = value
        session.commit()


def recipient_keys() -> list[str]:
    """Every cashout Pushover key, ignoring payment-method filters."""

    from db.connection import get_db
    from db.models import StaffCashoutNotifyRecipient

    with get_db() as session:
        rows = (
            session.query(StaffCashoutNotifyRecipient.pushover_user_key)
            .order_by(StaffCashoutNotifyRecipient.id.asc())
            .all()
        )
    seen: set[str] = set()
    keys: list[str] = []
    for (raw,) in rows:
        key = (raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


async def _fanout(message: str) -> bool:
    from bot.services.pushover_notify import notify_pushover

    keys = await asyncio.to_thread(recipient_keys)
    if not keys:
        logger.warning("unread_group_alert: no pushover recipients")
        return False
    ok_any = False
    for key in keys:
        ok = await notify_pushover(
            message,
            user=key,
            title=PUSHOVER_TITLE,
            priority=1,
            source=PUSHOVER_SOURCE,
        )
        ok_any = ok_any or ok
    return ok_any
