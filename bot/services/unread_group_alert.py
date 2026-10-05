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
BACKOFF_BASE_MINUTES = 5
BACKOFF_CAP_MINUTES = 30
FAILED_RETRY_SEC = 30.0
PUSHOVER_MAX_LEN = 1024
ESCALATION_SOURCE = "unread_group_alert"

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
    read_inbox_max_id: int = 0

    def snap(self) -> DialogSnap:
        return DialogSnap(
            unread_count=self.unread_count,
            title=self.title,
            archived=self.archived,
        )


def note_incoming(live: LiveDialog, message_id: int, title: str = "") -> bool:
    """Count a new incoming message. Already-read ids do not raise the badge."""

    if title:
        live.title = title
    if message_id <= live.top_message_id:
        return False
    live.top_message_id = message_id
    if message_id <= live.read_inbox_max_id:
        return False
    live.unread_count += 1
    return True


def note_inbox_read(live: LiveDialog, max_id: int, still_unread: int) -> bool:
    """Apply a club-account read. ``still_unread`` replaces the local badge."""

    if max_id > live.read_inbox_max_id:
        live.read_inbox_max_id = max_id
    new_count = max(0, still_unread)
    if live.unread_count == new_count:
        return False
    live.unread_count = new_count
    return True


def apply_server_unread(
    live: LiveDialog,
    *,
    unread_count: int,
    top_message_id: int,
    read_inbox_max_id: int,
    title: str = "",
    archived: bool | None = None,
) -> None:
    """Replace the badge with a ``messages.getPeerDialogs`` result."""

    live.unread_count = max(0, int(unread_count))
    if top_message_id > live.top_message_id:
        live.top_message_id = top_message_id
    if read_inbox_max_id > live.read_inbox_max_id:
        live.read_inbox_max_id = read_inbox_max_id
    if title:
        live.title = title
    if archived is not None:
        live.archived = archived


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


def backoff_delay_minutes(step: int) -> int:
    """5, 10, 20, then 30. The next step after 30 starts again at 5."""

    return min(BACKOFF_BASE_MINUTES * (2**step), BACKOFF_CAP_MINUTES)


def step_after_send(step: int) -> int:
    if BACKOFF_BASE_MINUTES * (2**step) >= BACKOFF_CAP_MINUTES:
        return 0
    return step + 1


def alert_action(
    count: int,
    last_sent_at: datetime | None,
    now: datetime,
    delay: timedelta,
) -> str:
    """``hold`` below 5, ``send`` when the gap since the last send has elapsed, ``wait`` inside it.

    The gap is not cleared when the count falls below 5.
    """

    if count < THRESHOLD:
        return "hold"
    if last_sent_at is None:
        return "send"
    if as_utc(now) - as_utc(last_sent_at) >= delay:
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
            raw = dialog.dialog
            top = int(getattr(raw, "top_message", 0) or 0)
            found[int(dialog.id)] = LiveDialog(
                unread_count=int(dialog.unread_count or 0),
                title=(dialog.title or "").strip(),
                archived=bool(dialog.archived),
                top_message_id=top,
                read_inbox_max_id=int(getattr(raw, "read_inbox_max_id", 0) or 0),
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
            if live is not None:
                note_incoming(live, message_id, title)
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
    logged: tuple[int, int, str] | None = None
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
            created = LiveDialog(
                unread_count=0,
                title=title,
                archived=False,
                top_message_id=0,
            )
            note_incoming(created, int(message_id), title)
            dialogs[int(chat_id)] = created
            changed = created.unread_count > 0
            logged = (int(chat_id), created.unread_count, created.title)
        elif note_incoming(current, int(message_id), title):
            changed = True
            logged = (int(chat_id), current.unread_count, current.title)
    if logged is not None and changed:
        logger.info(
            "unread_group_alert: incoming club=%s chat_id=%s unread=%s title=%s",
            club_key,
            logged[0],
            logged[1],
            logged[2],
        )
    if changed:
        schedule_evaluate()


async def _handle_raw(club_key: str, update: Any) -> None:
    if _stopped or club_key not in _ready:
        return
    if isinstance(update, UpdateDialogUnreadMark):
        return
    chat_id: int | None = None
    still: int | None = None
    max_id = 0
    if isinstance(update, UpdateReadChannelInbox):
        chat_id = int(get_peer_id(PeerChannel(update.channel_id)))
        still = int(update.still_unread_count)
        max_id = int(update.max_id)
    elif isinstance(update, UpdateReadHistoryInbox):
        chat_id = int(get_peer_id(update.peer))
        still = int(update.still_unread_count)
        max_id = int(update.max_id)
    if chat_id is None or still is None:
        return
    changed = False
    title = ""
    unread_now = 0
    async with _lock_for():
        if _stopped or club_key not in _ready:
            return
        current = _dialogs.get(club_key, {}).get(chat_id)
        if current is None:
            logger.info(
                "unread_group_alert: read club=%s chat_id=%s still=%s matched=0",
                club_key,
                chat_id,
                still,
            )
            return
        changed = note_inbox_read(current, max_id, still)
        title = current.title
        unread_now = current.unread_count
    logger.info(
        "unread_group_alert: read club=%s chat_id=%s still=%s unread=%s title=%s",
        club_key,
        chat_id,
        still,
        unread_now,
        title,
    )
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
    if len(rows) >= THRESHOLD:
        try:
            await _refresh_unread(connected)
        except FloodWaitError as exc:
            logger.warning(
                "unread_group_alert: refresh flood wait seconds=%s", exc.seconds
            )
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
    async with _lock_for():
        details = [
            f"{club_key} {chat_id} unread={live.unread_count} {live.title}"
            for club_key, dialogs in _dialogs.items()
            if club_key in ready
            for chat_id, live in dialogs.items()
            if live.unread_count >= 1
        ]
    logger.info(
        "unread_group_alert: count=%s %s",
        count,
        " | ".join(details),
    )
    now = datetime.now(timezone.utc)
    last_sent, step = await asyncio.to_thread(load_alert_control)
    delay = timedelta(minutes=backoff_delay_minutes(step))
    action = alert_action(count, last_sent, now, delay)
    if action == "hold":
        _cancel_timer()
        return
    if action == "wait":
        assert last_sent is not None
        remaining = delay - (now - as_utc(last_sent))
        _ensure_timer(max(remaining.total_seconds(), 1.0))
        return
    message = format_unread_alert(count, [title for _club, title in rows])
    if not await _post_escalation(message):
        logger.warning("unread_group_alert: escalation post failed count=%s", count)
        _schedule_timer(FAILED_RETRY_SEC, replace=True)
        return
    next_step = 0 if last_sent is None else step_after_send(step)
    wait_minutes = backoff_delay_minutes(next_step)
    await asyncio.to_thread(save_alert_control, now, next_step)
    logger.info(
        "unread_group_alert: sent count=%s next_minutes=%s",
        count,
        wait_minutes,
    )
    _schedule_timer(wait_minutes * 60, replace=True)


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


def _live_from_tl(dialog: Any, title: str) -> LiveDialog:
    return LiveDialog(
        unread_count=int(dialog.unread_count or 0),
        title=title,
        archived=dialog.folder_id is not None,
        top_message_id=int(dialog.top_message or 0),
        read_inbox_max_id=int(dialog.read_inbox_max_id or 0),
    )


async def _refresh_unread(connected: set[str]) -> None:
    """Re-read server badges for chats we still think are unread."""

    async with _lock_for():
        targets = {
            club_key: [
                chat_id for chat_id, live in dialogs.items() if live.unread_count >= 1
            ]
            for club_key, dialogs in _dialogs.items()
            if club_key in connected
        }
    for club_key, chat_ids in targets.items():
        client = _clients.get(club_key)
        if client is None or not client.is_connected() or not chat_ids:
            continue
        for start in range(0, len(chat_ids), 50):
            if _stopped:
                return
            await _refresh_chunk(client, club_key, chat_ids[start : start + 50])


async def _refresh_chunk(
    client: TelegramClient, club_key: str, chat_ids: list[int]
) -> None:
    peers = []
    resolved: list[int] = []
    for chat_id in chat_ids:
        try:
            peers.append(InputDialogPeer(await client.get_input_entity(chat_id)))
        except Exception:
            logger.info(
                "unread_group_alert: refresh skip club=%s chat_id=%s",
                club_key,
                chat_id,
            )
            continue
        resolved.append(chat_id)
    if not peers:
        return
    try:
        result = await client(GetPeerDialogsRequest(peers=peers))
    except FloodWaitError:
        raise
    except Exception:
        logger.exception(
            "unread_group_alert: refresh failed club=%s chats=%s",
            club_key,
            len(resolved),
        )
        return
    titles: dict[int, str] = {}
    for chat in result.chats:
        try:
            titles[int(get_peer_id(chat))] = _chat_title(chat)
        except Exception:
            continue
    async with _lock_for():
        if _stopped:
            return
        dialogs = _dialogs.get(club_key, {})
        for dialog in result.dialogs:
            chat_id = int(get_peer_id(dialog.peer))
            live = dialogs.get(chat_id)
            if live is None:
                continue
            apply_server_unread(
                live,
                unread_count=int(dialog.unread_count or 0),
                top_message_id=int(dialog.top_message or 0),
                read_inbox_max_id=int(dialog.read_inbox_max_id or 0),
                title=titles.get(chat_id, ""),
                archived=dialog.folder_id is not None,
            )


def _chat_title(chat: Any) -> str:
    return (getattr(chat, "title", None) or "").strip()


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
        _dialogs.setdefault(club_key, {})[chat_id] = _live_from_tl(dialog, title)


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


def load_alert_control() -> tuple[datetime | None, int]:
    from db.connection import get_db
    from db.models import UnreadGroupAlertControl

    with get_db() as session:
        row = session.get(UnreadGroupAlertControl, 1)
        if row is None:
            return None, 0
        sent = None if row.last_sent_at is None else as_utc(row.last_sent_at)
        return sent, int(row.backoff_step or 0)


def save_alert_control(last_sent_at: datetime | None, backoff_step: int) -> None:
    from db.connection import get_db
    from db.models import UnreadGroupAlertControl

    with get_db() as session:
        row = session.get(UnreadGroupAlertControl, 1)
        if row is None:
            row = UnreadGroupAlertControl(id=1)
            session.add(row)
        row.last_sent_at = last_sent_at
        row.backoff_step = int(backoff_step)
        session.commit()


async def _post_escalation(message: str) -> bool:
    from bot.services.slack_ops_notify import notify_slack_escalation

    return await notify_slack_escalation(message, source=ESCALATION_SOURCE)
