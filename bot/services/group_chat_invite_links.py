"""Resolve group-chat URLs for payment notifications (member-only t.me/c deep links)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from notification.chat_id import notification_group_chat_url, telegram_chat_id_variants
from notification.formatting import resolve_notification_linked_chat_id
from bot.services.support_group_chats import _normalize_invite_link

logger = logging.getLogger(__name__)

SUPPORT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def _support_bot_token() -> str | None:
    raw = (os.getenv(SUPPORT_BOT_TOKEN_ENV) or "").strip()
    return raw or None


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
    """Resolve a member-only hyperlink URL for a bound support group chat."""
    del club_id  # invite links are not used in staff notifications
    title = (group_title or "").strip()
    if not title:
        return None
    return notification_group_chat_url(int(telegram_chat_id))


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
