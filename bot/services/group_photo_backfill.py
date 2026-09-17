"""Hourly worker job: set club photos on active RT/CC support groups.

Universe is unique chats in ``support_group_idle_episode_state`` that join to
Round Table or Creator Club ``support_group_chats`` rows. Uses the live dm_gc
Telethon listener so /gc and other MTProto work stay up.

Default: 10 groups per hour. Groups that already have a Telegram photo are
recorded and skipped. Dry-run is not a mode here — the job only runs on the
worker and writes when it applies a photo.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from club_gc_settings import (
    CLUB_GC_CONFIG,
    GROUP_PHOTO_BACKFILL_CLUB_KEYS,
    get_group_photo_backfill_batch_size,
    get_group_photo_backfill_chat_id,
    get_group_photo_backfill_delay_sec,
    get_group_photo_backfill_first_delay_sec,
    get_group_photo_backfill_interval_sec,
    is_group_photo_backfill_enabled,
)
from notification.chat_id import telegram_chat_id_variants

logger = logging.getLogger(__name__)

_JOB_NAME = "group_photo_backfill"
_PHOTO_APP = None


@dataclass(frozen=True)
class PhotoBackfillCandidate:
    row_id: int
    club_key: str
    telegram_chat_id: int
    title: str


def classify_group_photo_action(*, entity_found: bool, has_photo: bool) -> str:
    if not entity_found:
        return "admin_not_in_group"
    if has_photo:
        return "already_has_photo"
    return "needs_photo"


def _variant_set(chat_id: int) -> set[int]:
    return set(telegram_chat_id_variants(int(chat_id)))


def match_idle_support_groups(
    *,
    idle_chat_ids: list[int],
    sgc_rows: list[PhotoBackfillCandidate],
    club_keys: tuple[str, ...] = GROUP_PHOTO_BACKFILL_CLUB_KEYS,
) -> list[PhotoBackfillCandidate]:
    """Join idle-episode chat ids to RT/CC support_group_chats rows."""

    idle_variants: set[int] = set()
    for cid in idle_chat_ids:
        idle_variants |= _variant_set(cid)

    seen: set[tuple[str, int]] = set()
    out: list[PhotoBackfillCandidate] = []
    for row in sgc_rows:
        if row.club_key not in club_keys:
            continue
        variants = _variant_set(row.telegram_chat_id)
        if not (variants & idle_variants):
            continue
        key = (row.club_key, min(variants))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def exclude_processed(
    candidates: list[PhotoBackfillCandidate],
    processed: list[tuple[str, int]],
) -> list[PhotoBackfillCandidate]:
    """Drop candidates whose club+chat (including id variants) already ran."""

    processed_keys: set[tuple[str, int]] = set()
    for club_key, chat_id in processed:
        for cid in _variant_set(chat_id):
            processed_keys.add((club_key, cid))

    out: list[PhotoBackfillCandidate] = []
    for row in candidates:
        if any((row.club_key, cid) in processed_keys for cid in _variant_set(row.telegram_chat_id)):
            continue
        out.append(row)
    return out


def load_photo_backfill_batch(
    *,
    limit: int,
    chat_id: int | None = None,
    club_keys: tuple[str, ...] = GROUP_PHOTO_BACKFILL_CLUB_KEYS,
) -> list[PhotoBackfillCandidate]:
    """Next unprocessed idle RT/CC support groups, oldest ``support_group_chats`` first."""

    from db.connection import get_db
    from db.models import GroupPhotoBackfillRow, SupportGroupChat, SupportGroupIdleEpisodeState

    with get_db() as session:
        idle_q = session.query(SupportGroupIdleEpisodeState.telegram_chat_id)
        if chat_id is not None:
            idle_q = idle_q.filter(
                SupportGroupIdleEpisodeState.telegram_chat_id.in_(
                    telegram_chat_id_variants(int(chat_id))
                )
            )
        idle_chat_ids = [int(cid) for (cid,) in idle_q.all() if cid is not None]

        idle_variants: set[int] = set()
        for cid in idle_chat_ids:
            idle_variants |= _variant_set(cid)

        sgc_q = session.query(SupportGroupChat).filter(
            SupportGroupChat.club_key.in_(club_keys)
        )
        if idle_variants:
            sgc_q = sgc_q.filter(SupportGroupChat.telegram_chat_id.in_(idle_variants))
        else:
            return []
        if chat_id is not None:
            sgc_q = sgc_q.filter(
                SupportGroupChat.telegram_chat_id.in_(
                    telegram_chat_id_variants(int(chat_id))
                )
            )
        sgc_rows = sgc_q.order_by(SupportGroupChat.id.asc()).all()
        candidates = [
            PhotoBackfillCandidate(
                row_id=int(row.id),
                club_key=row.club_key,
                telegram_chat_id=int(row.telegram_chat_id),
                title=row.telegram_chat_title or "",
            )
            for row in sgc_rows
        ]
        processed = [
            (row.club_key, int(row.telegram_chat_id))
            for row in session.query(GroupPhotoBackfillRow).all()
        ]

    matched = match_idle_support_groups(
        idle_chat_ids=idle_chat_ids,
        sgc_rows=candidates,
        club_keys=club_keys,
    )
    remaining = exclude_processed(matched, processed)
    return remaining[: max(0, int(limit))]


def record_photo_backfill_outcome(
    candidate: PhotoBackfillCandidate,
    *,
    status: str,
    error: str | None = None,
) -> None:
    from db.connection import get_db
    from db.models import GroupPhotoBackfillRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        existing = (
            session.query(GroupPhotoBackfillRow)
            .filter(
                GroupPhotoBackfillRow.club_key == candidate.club_key,
                GroupPhotoBackfillRow.telegram_chat_id == candidate.telegram_chat_id,
            )
            .first()
        )
        if existing is None:
            session.add(
                GroupPhotoBackfillRow(
                    club_key=candidate.club_key,
                    telegram_chat_id=candidate.telegram_chat_id,
                    support_group_chat_id=candidate.row_id,
                    group_title=candidate.title or None,
                    status=status,
                    error=(error[:2000] if error else None),
                    processed_at=now,
                )
            )
            return
        existing.status = status
        existing.support_group_chat_id = candidate.row_id
        existing.group_title = candidate.title or existing.group_title
        existing.error = error[:2000] if error else None
        existing.processed_at = now


async def resolve_group_entity(client, chat_id: int) -> Any | None:
    from telethon.tl.types import Channel, Chat

    from bot.services.mtproto_group_create import _with_single_flood_retry

    last_exc: Exception | None = None
    for cid in telegram_chat_id_variants(int(chat_id)):
        try:
            entity = await _with_single_flood_retry(
                f"get_entity:{cid}",
                lambda c=cid: client.get_entity(int(c)),
            )
            if isinstance(entity, (Channel, Chat)):
                return entity
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        logger.info(
            "group_photo_backfill: could not resolve chat_id=%s (%s)",
            chat_id,
            type(last_exc).__name__,
        )
    return None


async def backfill_one_group(
    client,
    cfg,
    candidate: PhotoBackfillCandidate,
) -> str:
    from bot.services.mtproto_group_create import (
        _apply_group_photo_entity,
        _with_single_flood_retry,
        entity_has_group_photo,
        resolve_repo_path,
    )
    from bot.services.support_group_chats import update_support_group_chat_row

    entity = await resolve_group_entity(client, candidate.telegram_chat_id)
    action = classify_group_photo_action(
        entity_found=entity is not None,
        has_photo=entity_has_group_photo(entity) if entity is not None else False,
    )
    if action == "admin_not_in_group":
        return action
    if action == "already_has_photo":
        if cfg.group_photo_path:
            update_support_group_chat_row(
                candidate.row_id,
                group_photo_path=cfg.group_photo_path,
            )
        return action

    if not cfg.group_photo_path:
        return "error"
    photo_abs = resolve_repo_path(cfg.group_photo_path)
    if not photo_abs.exists():
        return "error"

    await _with_single_flood_retry(
        "EditGroupPhoto",
        lambda: _apply_group_photo_entity(client, entity, photo_abs),
    )
    update_support_group_chat_row(
        candidate.row_id,
        group_photo_path=cfg.group_photo_path,
    )
    return "applied"


def _is_flood_exc(err: BaseException) -> bool:
    name = type(err).__name__
    if "FloodWait" in name:
        return True
    return "rate limit" in str(err).lower()


async def tick_async(
    *,
    limit: int | None = None,
    chat_id: int | None = None,
    delay_seconds: float | None = None,
) -> dict[str, int]:
    summary = {
        "considered": 0,
        "applied": 0,
        "already_has_photo": 0,
        "admin_not_in_group": 0,
        "error": 0,
        "skipped_listener_down": 0,
        "flood_wait": 0,
    }
    if not is_group_photo_backfill_enabled():
        return summary

    batch_size = int(limit) if limit is not None else get_group_photo_backfill_batch_size()
    pin = chat_id if chat_id is not None else get_group_photo_backfill_chat_id()
    delay = (
        float(delay_seconds)
        if delay_seconds is not None
        else get_group_photo_backfill_delay_sec()
    )

    candidates = load_photo_backfill_batch(limit=batch_size, chat_id=pin)
    if not candidates:
        logger.info("group_photo_backfill: no remaining idle RT/CC groups")
        return summary

    from bot.services.mtproto_dm_gc_listener import get_listener_client

    by_club: dict[str, list[PhotoBackfillCandidate]] = {}
    for row in candidates:
        by_club.setdefault(row.club_key, []).append(row)

    for club_key, rows in by_club.items():
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            logger.warning("group_photo_backfill: no MTProto config club=%s", club_key)
            for row in rows:
                record_photo_backfill_outcome(row, status="error", error="no_mtproto_config")
                summary["error"] += 1
                summary["considered"] += 1
            continue

        client = get_listener_client(club_key)
        if client is None or not client.is_connected():
            logger.info("group_photo_backfill: skip club=%s listener down", club_key)
            summary["skipped_listener_down"] += len(rows)
            continue

        for row in rows:
            summary["considered"] += 1
            try:
                status = await backfill_one_group(client, cfg, row)
            except Exception as exc:
                if _is_flood_exc(exc):
                    logger.warning(
                        "group_photo_backfill: FloodWait club=%s chat_id=%s — stopping tick",
                        club_key,
                        row.telegram_chat_id,
                    )
                    summary["flood_wait"] += 1
                    return summary
                status = f"error:{type(exc).__name__}"
                logger.warning(
                    "group_photo_backfill: %s club=%s chat_id=%s status=%s",
                    row.title or row.telegram_chat_id,
                    club_key,
                    row.telegram_chat_id,
                    status,
                )
                record_photo_backfill_outcome(row, status="error", error=type(exc).__name__)
                summary["error"] += 1
                continue

            record_photo_backfill_outcome(
                row,
                status=status if status != "error" else "error",
                error=status if status.startswith("error") else None,
            )
            if status == "applied":
                summary["applied"] += 1
                if delay > 0:
                    await asyncio.sleep(delay)
            elif status == "already_has_photo":
                summary["already_has_photo"] += 1
            elif status == "admin_not_in_group":
                summary["admin_not_in_group"] += 1
            else:
                summary["error"] += 1

            logger.info(
                "group_photo_backfill: %s club=%s chat_id=%s status=%s",
                row.title or row.telegram_chat_id,
                club_key,
                row.telegram_chat_id,
                status,
            )

    logger.info("group_photo_backfill tick %s", summary)
    return summary


def remove_group_photo_backfill_job() -> None:
    if _PHOTO_APP is None:
        return
    jobs = _PHOTO_APP.job_queue.get_jobs_by_name(_JOB_NAME)
    for job in jobs:
        job.schedule_removal()


def schedule_group_photo_backfill_tick() -> None:
    """Run tick on the dm_gc listener Telethon loop (not the PTB job-queue loop)."""

    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        logger.warning("group_photo_backfill: listener loop not running; skipping tick")
        return
    asyncio.run_coroutine_threadsafe(tick_async(), loop)


def group_photo_backfill_job_callback(context) -> None:
    schedule_group_photo_backfill_tick()


def setup_group_photo_backfill_job(app) -> None:
    """Schedule hourly photo backfill on the worker after the listener is up."""

    global _PHOTO_APP

    from club_gc_settings import is_dm_gc_listener_enabled

    if not is_dm_gc_listener_enabled():
        return
    if not is_group_photo_backfill_enabled():
        return
    if app.job_queue is None:
        logger.warning("group_photo_backfill: job_queue unavailable; job not scheduled")
        return

    _PHOTO_APP = app
    remove_group_photo_backfill_job()
    interval_sec = get_group_photo_backfill_interval_sec()
    first_delay_sec = get_group_photo_backfill_first_delay_sec()
    app.job_queue.run_repeating(
        group_photo_backfill_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name=_JOB_NAME,
    )
    logger.info(
        "group_photo_backfill job scheduled first_delay_sec=%s interval_sec=%s batch_size=%s",
        first_delay_sec,
        interval_sec,
        get_group_photo_backfill_batch_size(),
    )
