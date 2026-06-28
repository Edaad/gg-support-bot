"""Rename support groups via the club MTProto session (group creator).

The bot is invited as a regular member, so Bot API ``set_chat_title`` usually fails.
"""

from __future__ import annotations

import logging

from telegram import Bot

from club_gc_settings import get_club_gc_config_by_link_club_id
from bot.services.club import update_group_name
from bot.services.mtproto_group_create import (
    _is_channel_entity,
    _with_single_flood_retry,
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)
from bot.services.mtproto_track_contact import schedule_save_player_contact_named_group

logger = logging.getLogger(__name__)


async def rename_support_group_title(
    chat_id: int,
    club_id: int,
    new_title: str,
    *,
    bot: Bot | None = None,
    current_title: str | None = None,
) -> bool:
    """Best-effort rename: MTProto first (creator account), then Bot API fallback."""
    title = (new_title or "").strip()
    if not title:
        return False

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg:
        try:
            if await is_client_authorized(cfg):
                async with get_mtproto_lock(cfg.club_key):
                    client = make_client(cfg)
                    await client.connect()
                    try:
                        from telethon.tl.functions.channels import EditTitleRequest
                        from telethon.tl.functions.messages import EditChatTitleRequest
                        from telethon.utils import get_input_channel

                        entity = await client.get_entity(int(chat_id))

                        async def _edit():
                            if _is_channel_entity(entity):
                                await client(
                                    EditTitleRequest(
                                        channel=get_input_channel(entity),
                                        title=title[:255],
                                    )
                                )
                            else:
                                await client(
                                    EditChatTitleRequest(
                                        chat_id=int(entity.id),
                                        title=title[:255],
                                    )
                                )

                        await _with_single_flood_retry("EditGroupTitle", _edit)
                        update_group_name(int(chat_id), title)
                        schedule_save_player_contact_named_group(
                            chat_id=int(chat_id),
                            club_id=int(club_id),
                            chat_title=title,
                        )
                        logger.info(
                            "group rename via mtproto chat_id=%s club=%s old=%r new=%r",
                            chat_id,
                            cfg.club_key,
                            current_title,
                            title,
                        )
                        return True
                    finally:
                        await client.disconnect()
        except Exception:
            logger.warning(
                "group rename mtproto failed chat_id=%s club_id=%s new_title=%r",
                chat_id,
                club_id,
                title,
                exc_info=True,
            )

    if bot is not None:
        try:
            await bot.set_chat_title(int(chat_id), title)
            update_group_name(int(chat_id), title)
            schedule_save_player_contact_named_group(
                chat_id=int(chat_id),
                club_id=int(club_id),
                chat_title=title,
            )
            logger.info(
                "group rename via bot api chat_id=%s old=%r new=%r",
                chat_id,
                current_title,
                title,
            )
            return True
        except Exception:
            logger.warning(
                "group rename bot api failed chat_id=%s new_title=%r",
                chat_id,
                title,
                exc_info=True,
            )

    return False
