"""Resolve group-chat URLs for payment notifications (t.me/c, DB, Bot API on-demand)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from club_gc_settings import get_club_gc_config_by_link_club_id
from db.connection import get_db
from db.models import Club
from notification.chat_id import telegram_chat_id_variants, telegram_supergroup_chat_url
from notification.formatting import resolve_notification_linked_chat_id
from bot.services.support_group_chats import (
    _normalize_invite_link,
    fetch_invite_link_for_chat,
    upsert_support_group_invite_link,
)

logger = logging.getLogger(__name__)

SUPPORT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def _support_bot_token() -> str | None:
    raw = (os.getenv(SUPPORT_BOT_TOKEN_ENV) or "").strip()
    return raw or None


def _club_upsert_metadata(club_id: int | None) -> tuple[str, str]:
    if club_id is not None:
        cfg = get_club_gc_config_by_link_club_id(int(club_id))
        if cfg is not None:
            return cfg.club_key, cfg.club_display_name
        with get_db() as session:
            club = session.query(Club).filter_by(id=int(club_id)).one_or_none()
            if club and (club.name or "").strip():
                return f"club_{int(club_id)}", club.name.strip()
    return "unknown", "Unknown club"


async def _telegram_bot_api(
    method: str,
    payload: dict[str, Any],
    *,
    token: str,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()
    if not resp.is_success or not data.get("ok"):
        err = data.get("description") or f"HTTP {resp.status_code}"
        raise RuntimeError(str(err))
    return data


async def _invite_link_from_get_chat(chat_id: int, *, token: str) -> str | None:
    """Return primary invite link from getChat when the bot is a member."""
    try:
        data = await _telegram_bot_api("getChat", {"chat_id": int(chat_id)}, token=token)
    except RuntimeError:
        return None
    result = data.get("result") or {}
    return _normalize_invite_link(result.get("invite_link"))


async def export_invite_link_via_bot_api(chat_id: int) -> tuple[str | None, str | None]:
    """Resolve invite link via Bot API (getChat, then export). Returns (link, failure_reason)."""
    token = _support_bot_token()
    if not token:
        logger.warning("group_chat_invite: %s not set", SUPPORT_BOT_TOKEN_ENV)
        return None, "no_bot_token"

    variants = sorted(telegram_chat_id_variants(int(chat_id)), key=lambda x: (0 if str(x).startswith("-100") else 1, x))
    last_reason = "export_failed"
    for cid in variants:
        cached = await _invite_link_from_get_chat(int(cid), token=token)
        if cached:
            return cached, None

        try:
            data = await _telegram_bot_api(
                "exportChatInviteLink",
                {"chat_id": int(cid)},
                token=token,
            )
        except RuntimeError as e:
            last_reason = str(e)
            logger.warning(
                "group_chat_invite: exportChatInviteLink failed chat_id=%s: %s",
                cid,
                last_reason,
            )
            continue

        result = data.get("result") or {}
        link = _normalize_invite_link(result.get("invite_link"))
        if link:
            return link, None

    return None, last_reason


async def resolve_group_chat_notification_url(
    *,
    telegram_chat_id: int,
    group_title: str,
    club_id: int | None = None,
) -> str | None:
    """Resolve a hyperlink URL for a bound support group chat."""
    cid = int(telegram_chat_id)
    title = (group_title or "").strip()
    if not title:
        return None

    url = telegram_supergroup_chat_url(cid)
    if url:
        return url

    cached = fetch_invite_link_for_chat(cid, group_title=title)
    if cached:
        return cached

    link, reason = await export_invite_link_via_bot_api(cid)
    if not link:
        logger.warning(
            "group_chat_invite: no hyperlink url chat_id=%s title=%r reason=%s "
            "(backfill: scripts/backfill_support_group_invite_links.py --apply, "
            "or grant support bot admin + invite-link rights)",
            cid,
            title[:80],
            reason or "unknown",
        )
        return None

    if club_id is not None:
        club_key, club_display_name = _club_upsert_metadata(int(club_id))
        status, row_id = upsert_support_group_invite_link(
            club_key=club_key,
            club_display_name=club_display_name,
            telegram_chat_id=cid,
            telegram_chat_title=title,
            invite_link=link,
        )
        logger.info(
            "group_chat_invite: cached on-demand link chat_id=%s status=%s row_id=%s",
            cid,
            status,
            row_id,
        )

    return link


async def resolve_group_chat_url_for_payment(
    payment: object,
    *,
    group_title: str | None,
    telegram_chat_id: int | None = None,
) -> str | None:
    """Resolve notification URL for a bound payment row."""
    linked_cid = resolve_notification_linked_chat_id(
        payment,
        telegram_chat_id=telegram_chat_id,
    )
    title = (group_title or "").strip()
    if linked_cid is None or not title:
        return None
    club_id = getattr(payment, "club_id", None)
    return await resolve_group_chat_notification_url(
        telegram_chat_id=int(linked_cid),
        group_title=title,
        club_id=int(club_id) if club_id is not None else None,
    )
