"""Cash App deposit variant destination fields and default response template.

Dashboard stores ``cashapp_tag`` / ``cashapp_link`` on club_payment_tier_variants.
``cashapp_response_mode`` is ``default`` (fixed cover-memo template), ``text``,
or ``photo``. Matching prefers the stored cashtag over scraping response copy.
Stripe checkout variants do not use these fields.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Literal, Optional

from bot.services.venmo_variant_fields import pick_venmo_memo

CashAppResponseMode = Literal["default", "text", "photo"]

CASHAPP_RESPONSE_MODES: frozenset[str] = frozenset({"default", "text", "photo"})

_TAG_RE = re.compile(r"^\$[A-Za-z0-9_-]{1,30}$")
_LINK_RE = re.compile(r"^https://cash\.app/\$[A-Za-z0-9_-]{1,30}$")
# Loose scan for backfill / missing-link warnings (may appear mid-sentence).
_LINK_IN_TEXT_RE = re.compile(
    r"https?://(?:www\.)?cash\.app/\$?([A-Za-z0-9_-]{1,30})",
    re.IGNORECASE,
)
_BARE_TAG_IN_TEXT_RE = re.compile(
    r"(?<![a-zA-Z0-9])\$([A-Za-z0-9_-]{1,30})(?![a-zA-Z0-9])",
)

# Telegram HTML: memo is wrapped in <code> so players can tap to copy.
_DEFAULT_TEMPLATE = (
    "Cash App: {link}\n"
    "\n"
    "• Please put <code>{memo}</code> in the payment caption when sending.\n"
    "\n"
    "• Once sent, please send us a screenshot"
)


def normalize_cashapp_tag(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    if s.startswith("https://cash.app/"):
        s = s.rsplit("/", 1)[-1]
    if s and not s.startswith("$"):
        s = f"${s}"
    return s


def normalize_cashapp_link(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def cashtag_from_tag(tag: str) -> str:
    return normalize_cashapp_tag(tag).lstrip("$")


def cashtag_from_link(link: str) -> str | None:
    m = _LINK_RE.match(normalize_cashapp_link(link))
    if not m:
        return None
    return normalize_cashapp_link(link).rsplit("/", 1)[-1].lstrip("$")


def validate_cashapp_tag(raw: str | None) -> str:
    """Return normalized cashtag or raise ValueError."""
    tag = normalize_cashapp_tag(raw)
    if not _TAG_RE.match(tag):
        raise ValueError(
            "Cash App tag must be $cashtag (1–30 letters, digits, _ or -)."
        )
    return tag


def validate_cashapp_link(raw: str | None) -> str:
    """Return normalized link or raise ValueError."""
    link = normalize_cashapp_link(raw)
    if not _LINK_RE.match(link):
        raise ValueError(
            "Cash App link must be https://cash.app/$cashtag "
            "(https only, no www, query, or trailing slash)."
        )
    return link


def validate_cashapp_tag_and_link(
    tag: str | None,
    link: str | None,
) -> tuple[str, str]:
    """Validate both fields and require the same cashtag."""
    norm_tag = validate_cashapp_tag(tag)
    norm_link = validate_cashapp_link(link)
    if cashtag_from_tag(norm_tag) != cashtag_from_link(norm_link):
        raise ValueError("Cash App tag and link must name the same account.")
    return norm_tag, norm_link


def validate_cashapp_response_mode(raw: str | None) -> CashAppResponseMode:
    mode = (raw or "").strip().lower()
    if mode not in CASHAPP_RESPONSE_MODES:
        raise ValueError("Cash App response mode must be default, text, or photo.")
    return mode  # type: ignore[return-value]


def response_type_for_cashapp_mode(mode: CashAppResponseMode) -> str:
    if mode == "photo":
        return "photo"
    return "text"


def extract_unique_cashapp_link_from_text(*fields: str | None) -> str | None:
    """Return one canonical link if all found cashtags agree; else None."""
    found: set[str] = set()
    for field in fields:
        if not field:
            continue
        for m in _LINK_IN_TEXT_RE.finditer(field):
            found.add(m.group(1).lower())
        for m in _BARE_TAG_IN_TEXT_RE.finditer(field):
            found.add(m.group(1).lower())
    if len(found) != 1:
        return None
    cashtag = next(iter(found))
    return f"https://cash.app/${cashtag}"


def text_contains_cashapp_link(text: str | None, link: str) -> bool:
    """True when ``text`` contains the same cashtag as ``link``."""
    expected = cashtag_from_link(normalize_cashapp_link(link))
    if not expected or not text:
        return False
    for m in _LINK_IN_TEXT_RE.finditer(text):
        if m.group(1).lower() == expected:
            return True
    for m in _BARE_TAG_IN_TEXT_RE.finditer(text):
        if m.group(1).lower() == expected:
            return True
    return False


def build_default_cashapp_response_text(
    link: str,
    amount: Decimal | float | int | str,
    *,
    memo: str | None = None,
) -> str:
    """Player-facing default deposit copy (Telegram HTML) with link and memo."""
    norm_link = validate_cashapp_link(link)
    phrase = memo if memo is not None else pick_venmo_memo(amount)
    return _DEFAULT_TEMPLATE.format(
        link=html.escape(norm_link),
        memo=html.escape(phrase),
    )


def stored_cashapp_tag(variant) -> Optional[str]:
    """Prefer stored tag; else scrape response fields."""
    tag = getattr(variant, "cashapp_tag", None)
    if isinstance(tag, str) and tag.strip():
        try:
            return validate_cashapp_tag(tag)
        except ValueError:
            pass
    from bot.services.payment_method_binding import extract_cashapp_handle_from_text

    for field in (
        getattr(variant, "response_text", None),
        getattr(variant, "response_caption", None),
    ):
        if not isinstance(field, str):
            continue
        handle = extract_cashapp_handle_from_text(field)
        if handle:
            return handle
    return None


def stored_cashapp_link(variant) -> Optional[str]:
    """Prefer stored link; else scrape response fields."""
    link = getattr(variant, "cashapp_link", None)
    if isinstance(link, str) and link.strip():
        try:
            return validate_cashapp_link(link)
        except ValueError:
            pass
    from bot.services.payment_method_binding import extract_cashapp_url

    for field in (
        getattr(variant, "response_text", None),
        getattr(variant, "response_caption", None),
    ):
        if not isinstance(field, str):
            continue
        url = extract_cashapp_url(field)
        if url:
            try:
                return validate_cashapp_link(url)
            except ValueError:
                # extract_cashapp_url may return a slightly different shape
                handle = url.rstrip("/").rsplit("/", 1)[-1].lstrip("$").lower()
                if re.fullmatch(r"[a-z0-9_-]{1,30}", handle):
                    return f"https://cash.app/${handle}"
    return None
