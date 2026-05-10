"""Per-club configuration for `/gc` MTProto group creation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def _env_optional(key: str) -> str | None:
    v = os.getenv(key)
    if not v or not str(v).strip():
        return None
    return str(v).strip()


def _env_csv_tuple(key: str) -> tuple[str, ...]:
    raw = os.getenv(key, "")
    if not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    if not v or not v.strip():
        return default
    return v.strip()


def _env_optional_int(key: str) -> int | None:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _link_club_id_for_gc(env_key: str, *, default_dashboard_id: int) -> int:
    """Production defaults for ``clubs.id`` per `/gc` profile; env overrides."""

    parsed = _env_optional_int(env_key)
    return default_dashboard_id if parsed is None else parsed


def _invite_list(env_var: str, club_key: str) -> tuple[str, ...]:
    """Env GC_USERS_* overrides config.GC_USERS_TO_INVITE when non-empty."""

    csv = _env_csv_tuple(env_var)
    if csv:
        return csv
    try:

        import config as _cfg

        raw = getattr(_cfg, "GC_USERS_TO_INVITE", {}).get(club_key, ())

        return tuple(str(x).strip() for x in raw if str(x).strip())


    except Exception:

        return ()


def _nullable_path(key: str, default_rel: str) -> str | None:
    """If env explicitly set to empty, treat as unset (no photo)."""
    explicit = os.getenv(key)
    if explicit is not None:
        stripped = explicit.strip()
        return None if stripped in ("", "-", "none", "NONE") else stripped
    return default_rel


@dataclass(frozen=True)
class ClubGcConfig:
    club_key: str
    club_display_name: str
    command_admin_user_id: int
    mtproto_session: str
    mtproto_phone_number: str | None
    # Megagroup names are ``{RT|CC|GTO} / / {player label}`` (see mtproto_group_create.build_support_megagroup_title).
    # ``group_title`` env defaults remain for overrides if future code references them only.
    group_title: str
    group_photo_path: str | None
    users_to_add: tuple[str, ...]
    bot_account: str | None
    initial_group_message_template: str
    # Dashboard clubs.id — link megagroups from /gc so the bot sends welcome + member-join bundle.
    link_club_id: int


def build_club_gc_config() -> Mapping[str, ClubGcConfig]:
    bot_account = _env_optional("GC_BOT_ACCOUNT")

    return {
        "round_table": ClubGcConfig(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=int(
                os.getenv("GC_ADMIN_USER_ROUND_TABLE", "6713100304")
            ),
            mtproto_session=_env_str("GC_SESSION_ROUND_TABLE", "sessions/round_table.session"),
            mtproto_phone_number=_env_optional("MT_PROTO_PHONE_ROUND_TABLE"),
            group_title=_env_str("GC_GROUP_TITLE_ROUND_TABLE", "RT / New Player"),
            group_photo_path=_nullable_path(
                "GC_GROUP_PHOTO_ROUND_TABLE", "assets/group_photos/round_table.jpg"
            ),
            users_to_add=_invite_list("GC_USERS_ROUND_TABLE", "round_table"),
            bot_account=bot_account,
            initial_group_message_template=_env_str(
                "GC_INITIAL_MSG_ROUND_TABLE",
                "Group created. Invite link: {invite_link}",
            ),
            link_club_id=_link_club_id_for_gc("GC_LINK_CLUB_ID_ROUND_TABLE", default_dashboard_id=2),
        ),
        "creator_club": ClubGcConfig(
            club_key="creator_club",
            club_display_name="Creator Club",
            command_admin_user_id=int(
                os.getenv("GC_ADMIN_USER_CREATOR_CLUB", "8318575265")
            ),
            mtproto_session=_env_str("GC_SESSION_CREATOR_CLUB", "sessions/creator_club.session"),
            mtproto_phone_number=_env_optional("MT_PROTO_PHONE_CREATOR_CLUB"),
            group_title=_env_str("GC_GROUP_TITLE_CREATOR_CLUB", "CC / New Player"),
            group_photo_path=_nullable_path(
                "GC_GROUP_PHOTO_CREATOR_CLUB", "assets/group_photos/creator_club.jpg"
            ),
            users_to_add=_invite_list("GC_USERS_CREATOR_CLUB", "creator_club"),
            bot_account=bot_account,
            initial_group_message_template=_env_str(
                "GC_INITIAL_MSG_CREATOR_CLUB",
                "Group created. Invite link: {invite_link}",
            ),
            link_club_id=_link_club_id_for_gc("GC_LINK_CLUB_ID_CREATOR_CLUB", default_dashboard_id=3),
        ),
        "clubgto": ClubGcConfig(
            club_key="clubgto",
            club_display_name="ClubGTO",
            command_admin_user_id=int(os.getenv("GC_ADMIN_USER_CLUB_GTO", "7516419496")),
            mtproto_session=_env_str("GC_SESSION_CLUB_GTO", "sessions/clubgto.session"),
            mtproto_phone_number=_env_optional("MT_PROTO_PHONE_CLUB_GTO"),
            group_title=_env_str("GC_GROUP_TITLE_CLUB_GTO", "GTO / New Player"),
            group_photo_path=_nullable_path(
                "GC_GROUP_PHOTO_CLUB_GTO", "assets/group_photos/clubgto.jpg"
            ),
            users_to_add=_invite_list("GC_USERS_CLUB_GTO", "clubgto"),
            bot_account=bot_account,
            initial_group_message_template=_env_str(
                "GC_INITIAL_MSG_CLUB_GTO",
                "Group created. Invite link: {invite_link}",
            ),
            link_club_id=_link_club_id_for_gc("GC_LINK_CLUB_ID_CLUB_GTO", default_dashboard_id=4),
        ),
    }


CLUB_GC_CONFIG = build_club_gc_config()

_command_admin_ids: tuple[tuple[int, ClubGcConfig], ...] = tuple(
    sorted(
        [(cfg.command_admin_user_id, cfg) for cfg in CLUB_GC_CONFIG.values()],
        key=lambda x: x[0],
    )
)


def get_club_config_for_admin(telegram_user_id: int) -> ClubGcConfig | None:
    for uid, cfg in _command_admin_ids:
        if uid == telegram_user_id:
            return cfg
    return None


def gc_mtproto_operator_telegram_user_ids() -> frozenset[int]:
    """Club MTProto `/gc` admin Telegram user IDs (Round Table / Creator Club / ClubGTO)."""

    return frozenset(int(cfg.command_admin_user_id) for cfg in CLUB_GC_CONFIG.values())


def get_club_gc_config_by_link_club_id(dashboard_clubs_id: int) -> ClubGcConfig | None:
    """Maps ``clubs.id`` (dashboard) to `/gc` MTProto club profile when IDs match."""

    for cfg in CLUB_GC_CONFIG.values():
        if int(cfg.link_club_id) == int(dashboard_clubs_id):
            return cfg
    return None


def get_tg_mtproto_credentials() -> tuple[int, str]:
    """Telegram developer API credentials (shared across club MTProto sessions)."""
    api_id_raw = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "TG_API_ID and TG_API_HASH must be set in the environment for MTProto (/gc)."
        )
    api_id = int(api_id_raw)
    return api_id, api_hash


def is_dm_gc_listener_enabled() -> bool:
    """Telethon listens for outgoing /gc in admin→player DMs unless explicitly disabled.

    Default **on**. Set ``GC_DM_GC_LISTENER_ENABLED`` to ``false``, ``0``, ``no``, or ``off`` to turn off.
    Use a single bot worker when enabled (same MTProto session must not connect twice).
    """
    raw = os.getenv("GC_DM_GC_LISTENER_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def is_contact_save_enabled() -> bool:
    """Telethon saves player contacts from track/info flows unless explicitly disabled."""

    raw = os.getenv("GC_CONTACT_SAVE_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def is_dm_gc_verbose_logging() -> bool:
    """Extra ``INFO`` logs for outgoing-DM ``/gc`` (captures, bootstrap, success). Default off."""

    return os.getenv("GC_DM_GC_VERBOSE_LOGS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
