"""Route payment notifications to club bind chats."""

from __future__ import annotations

import logging
import os
from typing import Iterable, Literal, Optional

from bot.services.player_details import _shorthands_from_prefix_segment
from notification.chat_id import telegram_chat_ids_match
from notification.constants import (
    PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_ENV,
)

logger = logging.getLogger(__name__)

NotificationBucket = Literal["gto", "rt_at", "creator_club"]

BUCKET_GTO: NotificationBucket = "gto"
BUCKET_RT_AT: NotificationBucket = "rt_at"
BUCKET_CREATOR_CLUB: NotificationBucket = "creator_club"

_GTO_SHORTHANDS = frozenset({"GTO"})
_RT_AT_SHORTHANDS = frozenset({"RT", "AT"})
_CREATOR_SHORTHANDS = frozenset({"CC"})

_CC_AT_UNION_BUCKETS = frozenset({BUCKET_RT_AT, BUCKET_CREATOR_CLUB})
_ALL_CLUB_BUCKETS = frozenset({BUCKET_GTO, BUCKET_RT_AT, BUCKET_CREATOR_CLUB})


def _env_chat_id(env_key: str) -> int | None:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r", env_key, raw)
        return None


def gto_notification_chat_id() -> int | None:
    return _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV)


def rt_at_notification_chat_id() -> int | None:
    chat_id = _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_ENV)
    if chat_id is not None:
        return chat_id
    legacy = _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV)
    if legacy is not None:
        logger.warning(
            "%s is deprecated; use %s",
            PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV,
            PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_ENV,
        )
    return legacy


def creator_club_notification_chat_id() -> int | None:
    return _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB_ENV)


def club_binding_notification_chat_ids() -> list[int]:
    """Configured club bind chats (GTO, RT/AT, Creator Club)."""
    ids: list[int] = []
    for cid in (
        gto_notification_chat_id(),
        rt_at_notification_chat_id(),
        creator_club_notification_chat_id(),
    ):
        if cid is not None and cid not in ids:
            ids.append(int(cid))
    return ids


def configured_bind_notification_chat_ids() -> list[int]:
    """Chats where payment bind UX and /report are active."""
    return club_binding_notification_chat_ids()


def configured_notification_chat_ids() -> list[int]:
    """All configured Telegram staff notification bind chats."""
    return configured_bind_notification_chat_ids()


def canonical_notification_chat_id(chat_id: int) -> int | None:
    """Map a Telegram chat id to a configured bind chat, if any."""
    for configured in configured_bind_notification_chat_ids():
        if telegram_chat_ids_match(int(chat_id), int(configured)):
            return int(configured)
    return None


def is_bind_notification_chat_id(chat_id: int) -> bool:
    return canonical_notification_chat_id(int(chat_id)) is not None


def notification_buckets_for_title(title: str | None) -> frozenset[NotificationBucket]:
    """All club buckets referenced by a group title prefix."""
    raw = (title or "").strip()
    if not raw:
        return frozenset()
    first = raw.split("/", 1)[0]
    shorthands = _shorthands_from_prefix_segment(first)
    buckets: set[NotificationBucket] = set()
    if shorthands & _GTO_SHORTHANDS:
        buckets.add(BUCKET_GTO)
    if shorthands & _RT_AT_SHORTHANDS:
        buckets.add(BUCKET_RT_AT)
    if shorthands & _CREATOR_SHORTHANDS:
        buckets.add(BUCKET_CREATOR_CLUB)
    return frozenset(buckets)


def notification_bucket_for_title(title: str | None) -> NotificationBucket | None:
    """Single bucket when a title maps to exactly one club; else None."""
    buckets = notification_buckets_for_title(title)
    if len(buckets) == 1:
        return next(iter(buckets))
    return None


def notification_destination_bucket(
    titles: Iterable[str | None],
) -> NotificationBucket | None:
    """Single destination bucket for a set of titles, or None for broadcast."""
    buckets: set[NotificationBucket] = set()
    for title in titles:
        buckets |= set(notification_buckets_for_title(title))
    if len(buckets) == 1:
        return next(iter(buckets))
    return None


def notification_destination_buckets(
    titles: Iterable[str | None],
) -> frozenset[NotificationBucket]:
    combined: set[NotificationBucket] = set()
    for title in titles:
        combined |= set(notification_buckets_for_title(title))
    return frozenset(combined)


def _chat_id_for_bucket(bucket: NotificationBucket) -> int | None:
    if bucket == BUCKET_GTO:
        return gto_notification_chat_id()
    if bucket == BUCKET_RT_AT:
        return rt_at_notification_chat_id()
    if bucket == BUCKET_CREATOR_CLUB:
        return creator_club_notification_chat_id()
    return None


def _env_key_for_bucket(bucket: NotificationBucket) -> str:
    if bucket == BUCKET_GTO:
        return PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV
    if bucket == BUCKET_RT_AT:
        return PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_ENV
    return PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB_ENV


def _chat_ids_for_buckets(buckets: Iterable[NotificationBucket]) -> list[int]:
    ids: list[int] = []
    for bucket in (BUCKET_GTO, BUCKET_RT_AT, BUCKET_CREATOR_CLUB):
        if bucket not in buckets:
            continue
        club = _chat_id_for_bucket(bucket)
        if club is not None:
            chat_id = int(club)
            if chat_id not in ids:
                ids.append(chat_id)
        else:
            logger.warning(
                "%s not set; skipping %s payment notification chat",
                _env_key_for_bucket(bucket),
                bucket,
            )
    return ids


def _all_club_bind_chat_ids() -> list[int]:
    bind = club_binding_notification_chat_ids()
    if bind:
        return bind
    return []


def ingest_notification_titles(
    *,
    group_title: str | None = None,
    auto_bound: bool = False,
    ambiguous_candidates: Optional[Iterable[object]] = None,
) -> list[str]:
    if auto_bound:
        title = (group_title or "").strip()
        return [title] if title else []
    titles: list[str] = []
    for candidate in ambiguous_candidates or []:
        title = (getattr(candidate, "group_title", None) or "").strip()
        if title:
            titles.append(title)
    return titles


def resolve_notification_chat_ids(titles: Iterable[str | None]) -> list[int]:
    """Staff bind chats for these titles.

    Single-bucket titles go to that club chat. CC+AT union titles go to RT/AT
    and Creator Club. Cross-club mixed / unknown titles broadcast to all club
    bind chats.
    """
    title_list = [(t or "").strip() for t in titles if (t or "").strip()]
    if not title_list:
        return _all_club_bind_chat_ids()

    combined = notification_destination_buckets(title_list)
    if not combined:
        return _all_club_bind_chat_ids()

    if combined <= _CC_AT_UNION_BUCKETS:
        ids = _chat_ids_for_buckets(combined)
        if ids:
            return ids
        return _all_club_bind_chat_ids()

    if len(combined) == 1:
        bucket = next(iter(combined))
        club = _chat_id_for_bucket(bucket)
        if club is not None:
            return [int(club)]
        logger.warning(
            "%s not set; broadcasting to all club bind chats",
            _env_key_for_bucket(bucket),
        )
        return _all_club_bind_chat_ids()

    return _all_club_bind_chat_ids()


def resolve_notification_chat_id(titles: Iterable[str | None]) -> int | None:
    """First bind chat for these titles (legacy single-destination callers)."""
    ids = resolve_notification_chat_ids(titles)
    return ids[0] if ids else None


def resolve_ingest_notification_chat_ids(
    *,
    group_title: str | None = None,
    auto_bound: bool = False,
    ambiguous_candidates: Optional[Iterable[object]] = None,
) -> list[int]:
    return resolve_notification_chat_ids(
        ingest_notification_titles(
            group_title=group_title,
            auto_bound=auto_bound,
            ambiguous_candidates=ambiguous_candidates,
        )
    )


def resolve_ingest_notification_chat_id(
    *,
    group_title: str | None = None,
    auto_bound: bool = False,
    ambiguous_candidates: Optional[Iterable[object]] = None,
) -> int | None:
    """First bind chat for ingest (legacy single-destination callers)."""
    ids = resolve_ingest_notification_chat_ids(
        group_title=group_title,
        auto_bound=auto_bound,
        ambiguous_candidates=ambiguous_candidates,
    )
    return ids[0] if ids else None
