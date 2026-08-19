"""Route payment notifications to main vs GTO vs RT/CC/AT staff chats."""

from __future__ import annotations

import logging
import os
from typing import Iterable, Literal, Optional

from bot.services.player_details import _shorthands_from_prefix_segment
from notification.chat_id import telegram_chat_ids_match
from notification.constants import (
    PAYMENT_NOTIFICATION_CHAT_ID_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV,
)

logger = logging.getLogger(__name__)

NotificationBucket = Literal["gto", "rt_at_cc"]

BUCKET_GTO: NotificationBucket = "gto"
BUCKET_RT_AT_CC: NotificationBucket = "rt_at_cc"

_GTO_SHORTHANDS = frozenset({"GTO"})
_RT_AT_CC_SHORTHANDS = frozenset({"RT", "AT", "CC"})


def _env_chat_id(env_key: str) -> int | None:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r", env_key, raw)
        return None


def main_notification_chat_id() -> int | None:
    return _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_ENV)


def gto_notification_chat_id() -> int | None:
    return _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV)


def rt_at_cc_notification_chat_id() -> int | None:
    return _env_chat_id(PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV)


def configured_notification_chat_ids() -> list[int]:
    ids: list[int] = []
    for cid in (
        main_notification_chat_id(),
        gto_notification_chat_id(),
        rt_at_cc_notification_chat_id(),
    ):
        if cid is not None and cid not in ids:
            ids.append(int(cid))
    return ids


def canonical_notification_chat_id(chat_id: int) -> int | None:
    """Map a Telegram chat id to the configured staff-notification chat, if any."""
    for configured in configured_notification_chat_ids():
        if telegram_chat_ids_match(int(chat_id), int(configured)):
            return int(configured)
    return None


def notification_bucket_for_title(title: str | None) -> NotificationBucket | None:
    """Club-notification bucket from the first `/` segment of a group title.

    Does not require a GG player id (megagroups like ``GTO / / Player`` still match).
    A single title with both GTO and RT/AT/CC tokens has no bucket.
    """
    raw = (title or "").strip()
    if not raw:
        return None
    first = raw.split("/", 1)[0]
    shorthands = _shorthands_from_prefix_segment(first)
    has_gto = bool(shorthands & _GTO_SHORTHANDS)
    has_rt_at_cc = bool(shorthands & _RT_AT_CC_SHORTHANDS)
    if has_gto and has_rt_at_cc:
        return None
    if has_gto:
        return BUCKET_GTO
    if has_rt_at_cc:
        return BUCKET_RT_AT_CC
    return None


def notification_destination_bucket(
    titles: Iterable[str | None],
) -> NotificationBucket | None:
    """Single destination bucket for a set of titles, or None for main.

    None means unknown, mixed GTO+RT/CC/AT, or no classifiable titles.
    """
    buckets: set[NotificationBucket] = set()
    for title in titles:
        bucket = notification_bucket_for_title(title)
        if bucket is not None:
            buckets.add(bucket)
    if buckets == {BUCKET_GTO}:
        return BUCKET_GTO
    if buckets == {BUCKET_RT_AT_CC}:
        return BUCKET_RT_AT_CC
    return None


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


def resolve_notification_chat_id(titles: Iterable[str | None]) -> int | None:
    """Staff chat for these titles. Missing/invalid club env falls back to main."""
    main = main_notification_chat_id()
    dest = notification_destination_bucket(titles)
    if dest == BUCKET_GTO:
        club = gto_notification_chat_id()
        if club is not None:
            return club
        logger.warning(
            "%s not set; falling back to main payment notification chat",
            PAYMENT_NOTIFICATION_CHAT_ID_GTO_ENV,
        )
        return main
    if dest == BUCKET_RT_AT_CC:
        club = rt_at_cc_notification_chat_id()
        if club is not None:
            return club
        logger.warning(
            "%s not set; falling back to main payment notification chat",
            PAYMENT_NOTIFICATION_CHAT_ID_RT_AT_CC_ENV,
        )
        return main
    return main


def resolve_ingest_notification_chat_id(
    *,
    group_title: str | None = None,
    auto_bound: bool = False,
    ambiguous_candidates: Optional[Iterable[object]] = None,
) -> int | None:
    return resolve_notification_chat_id(
        ingest_notification_titles(
            group_title=group_title,
            auto_bound=auto_bound,
            ambiguous_candidates=ambiguous_candidates,
        )
    )
