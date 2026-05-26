# config.py
#
# Shared bot settings. For MTProto `/gc` (support megagroup creation), club-specific
# fields live in club_gc_settings.py — see re-exports at the bottom of this file.

ADMIN_USER_IDS = [
    493310710,  # edaad personal
    6713100304,  # rtsupport
    8318575265,  # ccsupport
    7516419496,  # gtosupport
]

# Group title tracking: shorthand -> canonical clubs.name value
# Example group title: "GTO / 8190-5287 / ThePirate343"
CLUB_SHORTHAND_TO_NAME = {
    "GTO": "ClubGTO",
    "RT": "Round Table",
    "AT": "Round Table",
    "CC": "Creator Club",
}

# --- `/gc` (MTProto) ----------------------------------------------------------
# Who may run /gc is NOT granted by ADMIN_USER_IDS alone; see club_gc_settings.
# Users to auto-invite: edit GC_USERS_TO_INVITE in club_gc_settings.py, or set
# GC_USERS_* in .env to add more (merged with defaults). Keys: round_table, creator_club, clubgto.

from club_gc_settings import (  # noqa: E402
    CLUB_GC_CONFIG,
    ClubGcConfig,
    GC_USERS_TO_INVITE,
    get_club_config_for_admin,
    get_tg_mtproto_credentials,
)

__all__ = [
    "ADMIN_USER_IDS",
    "CLUB_SHORTHAND_TO_NAME",
    "GC_USERS_TO_INVITE",
    "CLUB_GC_CONFIG",
    "ClubGcConfig",
    "get_club_config_for_admin",
    "get_tg_mtproto_credentials",
]
