"""Parse Telegram group chat id arguments for admin GC lookup commands."""

from __future__ import annotations


def parse_gc_chat_id_args(args: list[str]) -> int | None:
    """Accept ``/cmd -100…`` or ``/cmd gc_id -100…``."""
    rest = list(args)
    if rest and rest[0].lower() in ("tg_gc_id", "gc_id", "chat_id", "gc_chat_id"):
        rest = rest[1:]
    if not rest:
        return None
    try:
        return int(rest[0].strip())
    except ValueError:
        return None
