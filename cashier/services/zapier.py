"""Format group titles as CLUB / PLAYER_ID / PLAYER_NAME (bonus + audit)."""

from __future__ import annotations

from typing import Optional

from bot.services.player_details import parse_tracking_title


def build_zapier_name(group_title: str) -> Optional[str]:
    """Build CLUB / PLAYER_ID / PLAYER_NAME from group title."""
    parsed = parse_tracking_title(group_title)
    if not parsed:
        return None
    shorthand, gg_player_id = parsed
    parts = [p.strip() for p in group_title.split("/") if p.strip()]
    player_name = parts[2] if len(parts) >= 3 else ""
    return f"{shorthand} / {gg_player_id} / {player_name}"
