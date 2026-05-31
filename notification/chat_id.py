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
