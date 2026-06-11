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


# Default `/gc` staff invitees (also excluded when finding the sole player).
# `config.py` re-exports this; keep in sync when editing invite lists.
GC_USERS_TO_INVITE: dict[str, tuple[str, ...]] = {
    "round_table": ("@RoundTableSupport3", "@YTranslateBot"),
    "creator_club": ("@CreatorClubSupport3", "@twocardcashier", "@YTranslateBot"),
    "clubgto": ("@ClubGTOAdmin", "@YTranslateBot"),
}

_GC_USERS_ENV_BY_CLUB_KEY: dict[str, str] = {
    "round_table": "GC_USERS_ROUND_TABLE",
    "creator_club": "GC_USERS_CREATOR_CLUB",
    "clubgto": "GC_USERS_CLUB_GTO",
}


def _invite_list(env_var: str, club_key: str) -> tuple[str, ...]:
    """Return invite/exclusion list for `/gc` staff accounts.

    We treat configured staff invitees as *exclusions* when searching for the sole
    player in a support group. `GC_USERS_*` may be used to add more accounts, but
    should not accidentally drop the repo defaults.
    """

    csv = _env_csv_tuple(env_var)
    defaults = tuple(
        str(x).strip() for x in GC_USERS_TO_INVITE.get(club_key, ()) if str(x).strip()
    )

    merged = list(defaults) + list(csv)
    seen: set[str] = set()
    out: list[str] = []
    for m in merged:
        key = str(m).strip()
        if not key:
            continue
        norm = key.lower()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(key)
    return tuple(out)


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


def get_gc_users_to_add(cfg: ClubGcConfig) -> tuple[str, ...]:
    """Resolve staff invite list at runtime (env + defaults).

    Prefer this over ``cfg.users_to_add`` when exclusion must reflect current env.
    """

    env_key = _GC_USERS_ENV_BY_CLUB_KEY.get(cfg.club_key, "")
    if not env_key:
        return cfg.users_to_add
    return _invite_list(env_key, cfg.club_key)


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


def is_mtproto_enabled() -> bool:
    """Master switch for Telethon on the bot worker (listener + contact save).

    Default **on**. Set ``GC_MTPROTO_ENABLED`` to ``false``, ``0``, ``no``, or ``off`` on
    Heroku (or locally) while running MTProto scripts against production — e.g.
    ``scripts/backfill_support_group_invite_links.py`` — so the worker does not hold
    the same session (``AuthKeyDuplicatedError``).
    """
    raw = os.getenv("GC_MTPROTO_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def is_dm_gc_listener_enabled() -> bool:
    """Telethon listens for outgoing /gc in admin→player DMs unless explicitly disabled.

    Default **on**. Set ``GC_DM_GC_LISTENER_ENABLED`` to ``false``, ``0``, ``no``, or ``off`` to turn off.
    Also off when ``GC_MTPROTO_ENABLED`` is false. Use a single bot worker when enabled
    (same MTProto session must not connect twice).
    """
    if not is_mtproto_enabled():
        return False
    raw = os.getenv("GC_DM_GC_LISTENER_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def is_contact_save_enabled() -> bool:
    """Telethon saves player contacts from title change, /track, and /info unless explicitly disabled."""

    if not is_mtproto_enabled():
        return False
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


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def get_mtproto_telethon_client_kwargs() -> dict[str, int | float | bool]:
    """Kwargs passed to every ``TelegramClient`` (listener + one-shot MTProto ops)."""

    return {
        "connection_retries": _env_int("GC_MTPROTO_CONNECTION_RETRIES", 5),
        "retry_delay": _env_float("GC_MTPROTO_RETRY_DELAY", 1.0),
        "request_retries": _env_int("GC_MTPROTO_REQUEST_RETRIES", 5),
        "auto_reconnect": _env_bool("GC_MTPROTO_AUTO_RECONNECT", True),
    }


def is_migration_recovery_enabled() -> bool:
    """Background batch re-add for migrated supergroups (worker job_queue)."""

    if not is_dm_gc_listener_enabled():
        return False
    if not _env_bool("GC_MIGRATION_RECOVERY_ENABLED", default=False):
        return False
    from bot.services.migration_recovery import is_migration_recovery_auto_disabled

    return not is_migration_recovery_auto_disabled()


def get_migration_recovery_interval_sec() -> int:
    return max(60, _env_int("GC_MIGRATION_RECOVERY_INTERVAL_SEC", 300))


def get_migration_recovery_batch_size() -> int:
    return max(1, min(_env_int("GC_MIGRATION_RECOVERY_BATCH_SIZE", 5), 20))


def get_migration_recovery_invite_delay_sec() -> float:
    return max(0.0, _env_float("GC_MIGRATION_RECOVERY_INVITE_DELAY_SEC", 2.0))


def get_dm_gc_listener_restart_config() -> tuple[float, float, float]:
    """``(initial_delay_sec, max_delay_sec, backoff_multiplier)`` for listener supervision."""

    initial = _env_float("GC_DM_GC_LISTENER_RESTART_DELAY_SEC", 5.0)
    max_delay = _env_float("GC_DM_GC_LISTENER_RESTART_DELAY_MAX_SEC", 120.0)
    multiplier = _env_float("GC_DM_GC_LISTENER_RESTART_BACKOFF", 2.0)
    if initial < 1.0:
        initial = 1.0
    if max_delay < initial:
        max_delay = initial
    if multiplier < 1.0:
        multiplier = 1.0
    return initial, max_delay, multiplier
