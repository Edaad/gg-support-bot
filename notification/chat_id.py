"""Telegram chat id equivalence (supergroup -100… vs legacy forms)."""

from __future__ import annotations


def telegram_chat_id_variants(chat_id: int) -> set[int]:
    """Return known equivalent ids for the same Telegram chat."""
    cid = int(chat_id)
    variants = {cid}
    s = str(cid)
    if s.startswith("-100") and len(s) > 4:
        rest = s[4:]
        if rest.isdigit():
            variants.add(int(f"-{rest}"))
    elif s.startswith("-") and not s.startswith("-100"):
        rest = s[1:]
        if rest.isdigit():
            variants.add(int(f"-100{rest}"))
    return variants


def telegram_chat_ids_match(a: int, b: int) -> bool:
    return bool(telegram_chat_id_variants(a) & telegram_chat_id_variants(b))


def telegram_supergroup_chat_url(chat_id: int) -> str | None:
    """Build a ``t.me/c/…`` URL for a Telegram supergroup or channel chat id.

    Basic groups (Bot API ``type=group``, id like ``-5287778428``) do not support
    ``t.me/c/…`` links — only supergroups/channels (id ``-100…``) do.
    """
    cid = int(chat_id)
    if cid >= 0:
        return None
    s = str(cid)
    if not (s.startswith("-100") and len(s) > 4):
        return None
    internal = s[4:]
    if not internal.isdigit():
        return None
    return f"https://t.me/c/{internal}"


def is_joinable_invite_url(url: str | None) -> bool:
    """True when URL is a Telegram invite link that lets non-members join."""
    raw = (url or "").strip().lower()
    if not raw:
        return False
    if "joinchat/" in raw:
        return True
    return "t.me/+" in raw or "telegram.me/+" in raw


def notification_group_chat_url(chat_id: int) -> str | None:
    """Member-only deep link for notifications; never a joinable invite link."""
    cid = int(chat_id)
    variants = sorted(
        telegram_chat_id_variants(cid),
        key=lambda x: (0 if str(x).startswith("-100") else 1, x),
    )
    for variant in variants:
        url = telegram_supergroup_chat_url(variant)
        if url:
            return url
    return None
