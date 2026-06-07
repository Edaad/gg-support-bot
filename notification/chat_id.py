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
    """Build a ``t.me/c/…`` URL for a Telegram supergroup or legacy group chat id."""
    cid = int(chat_id)
    if cid >= 0:
        return None
    s = str(cid)
    if s.startswith("-100") and len(s) > 4:
        internal = s[4:]
    elif s.startswith("-"):
        internal = s[1:]
    else:
        return None
    if not internal.isdigit():
        return None
    return f"https://t.me/c/{internal}"


def format_linked_chat_footer(telegram_chat_id: int | None) -> str:
    """HTML footer linking to a bound support group chat, or empty when not linked."""
    if telegram_chat_id is None:
        return ""
    url = telegram_supergroup_chat_url(int(telegram_chat_id))
    if not url:
        return ""
    return f'\n<a href="{url}">Open group chat</a>'


def resolve_notification_linked_chat_id(
    payment: object,
    *,
    telegram_chat_id: int | None = None,
) -> int | None:
    if telegram_chat_id is not None:
        return int(telegram_chat_id)
    raw = getattr(payment, "telegram_chat_id", None)
    return int(raw) if raw is not None else None
