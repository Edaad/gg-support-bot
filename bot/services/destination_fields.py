"""One destination per deposit variant: tag, link, and response mode.

The method slug chooses the validator. ``venmo_*`` and ``cashapp_*`` columns
stay filled as mirrors so the previous release can still read them.
"""

from __future__ import annotations

import re
from typing import Any

DESTINATION_SLUGS = frozenset({"venmo", "cashapp", "zelle"})

_ZELLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LEGACY_FIELDS = (
    "venmo_tag",
    "venmo_link",
    "venmo_response_mode",
    "cashapp_tag",
    "cashapp_link",
    "cashapp_response_mode",
)


def validate_zelle_tag(raw: str | None) -> str:
    """Return a normalized Zelle email or phone, or raise ValueError."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("Zelle tag must be an email or a phone number.")
    if "@" in s:
        email = s.lower().rstrip(".,;")
        if not _ZELLE_EMAIL_RE.match(email) or len(email) > 200:
            raise ValueError("Zelle tag must be an email or a phone number.")
        return email
    digits = re.sub(r"\D", "", s)
    if len(digits) < 10 or len(digits) > 15:
        raise ValueError("Zelle tag must be an email or a phone number.")
    return digits


def text_attr(obj: Any, name: str) -> str | None:
    if isinstance(obj, dict):
        raw = obj.get(name)
    else:
        raw = getattr(obj, name, None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def read_tag(variant: Any) -> str | None:
    return (
        text_attr(variant, "tag")
        or text_attr(variant, "venmo_tag")
        or text_attr(variant, "cashapp_tag")
    )


def read_link(variant: Any) -> str | None:
    return (
        text_attr(variant, "link")
        or text_attr(variant, "venmo_link")
        or text_attr(variant, "cashapp_link")
    )


def read_response_mode(variant: Any) -> str | None:
    return (
        text_attr(variant, "response_mode")
        or text_attr(variant, "venmo_response_mode")
        or text_attr(variant, "cashapp_response_mode")
    )


def legacy_mirror(
    slug: str,
    tag: str | None,
    link: str | None,
    mode: str | None,
) -> dict[str, str | None]:
    """Shared columns plus the rail-specific mirrors the previous release reads."""
    out: dict[str, str | None] = {name: None for name in LEGACY_FIELDS}
    out["tag"] = tag
    out["link"] = link
    out["response_mode"] = mode
    if slug == "venmo":
        out["venmo_tag"] = tag
        out["venmo_link"] = link
        out["venmo_response_mode"] = mode
    elif slug == "cashapp":
        out["cashapp_tag"] = tag
        out["cashapp_link"] = link
        out["cashapp_response_mode"] = mode
    return out


def stored_zelle_recipient(variant: Any) -> str | None:
    """Prefer the stored tag; else scrape response text."""
    raw = text_attr(variant, "tag")
    if raw:
        try:
            return validate_zelle_tag(raw)
        except ValueError:
            pass
    from bot.services.payment_method_binding import extract_zelle_recipient_from_text

    for name in ("response_text", "response_caption"):
        recipient = extract_zelle_recipient_from_text(text_attr(variant, name))
        if recipient:
            return recipient
    return None
