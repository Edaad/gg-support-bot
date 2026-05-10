"""MTProto best-effort: save group chat title as Telegram contact first name for the sole player."""

from __future__ import annotations

import asyncio
import logging

from club_gc_settings import (
    ClubGcConfig,
    gc_mtproto_operator_telegram_user_ids,
    get_club_gc_config_by_link_club_id,
    is_contact_save_enabled,
)
from config import ADMIN_USER_IDS
from bot.services.mtproto_group_create import (
    _with_single_flood_retry,
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)

logger = logging.getLogger(__name__)

# Telegram contact first name conservative cap (characters).
_CONTACT_FIRST_MAX = 64


def schedule_save_player_contact_named_group(
    *,
    chat_id: int,
    club_id: int | None,
    chat_title: str | None,
) -> None:
    """Fire-and-forget; never raises. Call only from `/info` (see track.info_handler)."""
    if not is_contact_save_enabled():
        return
    if club_id is None:
        return
    title_t = (chat_title or "").strip()
    if not title_t:
        return

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if not cfg:
        logger.debug(
            "contact_save: no ClubGcConfig for dashboard club_id=%s", club_id
        )
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("contact_save: no running event loop chat_id=%s", chat_id)
        return

    async def _run_wrapped():
        try:
            await _maybe_save_player_contact(chat_id=chat_id, cfg=cfg, chat_title=title_t)
        except Exception:
            logger.exception(
                "contact_save: unexpected error chat_id=%s club=%s",
                chat_id,
                cfg.club_key,
            )

    loop.create_task(_run_wrapped(), name=f"contact-save-{chat_id}")


def _truncate_first_name(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    if len(s) <= _CONTACT_FIRST_MAX:
        return s
    return s[: _CONTACT_FIRST_MAX - 1].rstrip() + "…"


async def _resolve_invitee_user_ids(client, cfg: ClubGcConfig) -> set[int]:
    out: set[int] = set()
    markers: list[str] = list(cfg.users_to_add)
    if cfg.bot_account and str(cfg.bot_account).strip():
        bn = str(cfg.bot_account).strip()
        markers.append(bn)
    seen: set[str] = set()
    for marker in markers:
        m = marker.strip()
        key = m.lower().lstrip("@")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            ent = await _with_single_flood_retry(
                f"invite_entity:{key}",
                lambda: client.get_entity(m),
            )
            uid = getattr(ent, "id", None)
            if uid is not None:
                out.add(int(uid))
        except Exception as e:
            logger.warning(
                "contact_save: unresolved users_to_add marker %s: %s",
                m[:40],
                type(e).__name__,
            )
    return out


async def _admin_user_ids(client, channel_ent) -> set[int]:
    from telethon.tl.types import ChannelParticipantsAdmins

    ids: set[int] = set()

    async def walk():
        async for u in client.iter_participants(
            channel_ent, filter=ChannelParticipantsAdmins()
        ):
            if u and getattr(u, "id", None) is not None:
                ids.add(int(u.id))

    try:
        await _with_single_flood_retry("iter_admin_participants", walk)
    except Exception as e:
        logger.warning(
            "contact_save: admin list failed chat=%s: %s",
            getattr(channel_ent, "id", "?"),
            type(e).__name__,
        )
    return ids


async def _maybe_save_player_contact(*, chat_id: int, cfg: ClubGcConfig, chat_title: str) -> None:
    try:
        from bot.services.mtproto_group_create import get_tg_mtproto_credentials

        get_tg_mtproto_credentials()
    except RuntimeError:
        logger.debug("contact_save: TG_API_ID/TG_API_HASH unset")
        return

    fn = _truncate_first_name(chat_title)
    if not fn:
        return

    if not await is_client_authorized(cfg):
        logger.info("contact_save: MTProto unauthorized club=%s", cfg.club_key)
        return

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            try:
                me = await client.get_me()
                self_id = int(me.id) if me else None
            except Exception:
                self_id = None

            chan = await _with_single_flood_retry(
                "get_entity_chat",
                lambda: client.get_entity(chat_id),
            )

            admin_ids = await _admin_user_ids(client, chan)
            invite_ids = await _resolve_invitee_user_ids(client, cfg)

            skip_operators = gc_mtproto_operator_telegram_user_ids()
            skip_dashboard_admins = frozenset(int(x) for x in ADMIN_USER_IDS)

            candidates: list = []

            async def collect():
                async for u in client.iter_participants(chan):
                    if not u or getattr(u, "bot", False):
                        continue
                    uid = getattr(u, "id", None)
                    if uid is None:
                        continue
                    uid_int = int(uid)
                    if self_id is not None and uid_int == self_id:
                        continue
                    if uid_int in admin_ids:
                        continue
                    if uid_int in invite_ids:
                        continue
                    # MTProto `/gc` club accounts usually stay in support groups but are not
                    # admins; excluding them restores "exactly one player" candidate semantics.
                    if uid_int in skip_operators:
                        continue
                    if uid_int in skip_dashboard_admins:
                        continue
                    candidates.append(u)

            try:
                await _with_single_flood_retry("iter_participants", collect)
            except Exception as e:
                logger.warning(
                    "contact_save: list participants chat_id=%s: %s",
                    chat_id,
                    type(e).__name__,
                )
                return

            if len(candidates) != 1:
                preview = ",".join(
                    str(getattr(u, "id", "?"))
                    for u in candidates[:6]
                )
                logger.warning(
                    "contact_save: skip chat_id=%s club=%s candidate_count=%s ids_sample=%s",
                    chat_id,
                    cfg.club_key,
                    len(candidates),
                    preview,
                )
                return

            user_obj = candidates[0]
            from telethon.tl import functions

            inp = await client.get_input_entity(user_obj)
            try:
                await _with_single_flood_retry(
                    "AddContactRequest",
                    lambda: client(
                        functions.contacts.AddContactRequest(
                            id=inp,
                            first_name=fn,
                            last_name="",
                            phone="",
                            add_phone_privacy_exception=False,
                        )
                    ),
                )
                logger.info(
                    "contact_save: saved club=%s player_user_id=%s",
                    cfg.club_key,
                    getattr(user_obj, "id", "?"),
                )
            except Exception as e:
                logger.warning(
                    "contact_save: AddContact failed club=%s: %s",
                    cfg.club_key,
                    type(e).__name__,
                )
        finally:
            await client.disconnect()
