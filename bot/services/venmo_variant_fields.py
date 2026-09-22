"""Venmo deposit variant destination fields and default response template.

Dashboard stores ``venmo_tag`` / ``venmo_link`` on club_payment_tier_variants.
``venmo_response_mode`` is ``default`` (fixed cover-memo template), ``text``,
or ``photo``. Matching prefers the stored tag over scraping response copy.
"""

from __future__ import annotations

import html
import random
import re
from decimal import Decimal
from typing import Literal, Optional

VenmoResponseMode = Literal["default", "text", "photo"]

VENMO_RESPONSE_MODES: frozenset[str] = frozenset({"default", "text", "photo"})

_TAG_RE = re.compile(r"^@[A-Za-z0-9_-]{2,30}$")
_LINK_RE = re.compile(r"^https://venmo\.com/u/[A-Za-z0-9_-]{2,30}$")
# Loose scan for backfill / missing-link warnings (may appear mid-sentence).
_LINK_IN_TEXT_RE = re.compile(
    r"https://venmo\.com/u/([A-Za-z0-9_-]{2,30})",
    re.IGNORECASE,
)

MEMO_LOW: tuple[str, ...] = (
    "Electric bill split",
    "Wifi/internet share",
    "Dinner split",
    "Netflix/Spotify split",
    "Coffee run",
    "Gas money",
    "Owed you from lunch",
    "Paying you back",
    "Birthday gift",
    "Wedding gift",
    "Graduation gift",
    "Holiday gift",
    "Groceries",
    "Love you Mom/Dad",
    "Concert ticket reimbursement",
    "Group gift",
)

MEMO_MEDIUM: tuple[str, ...] = (
    "Helping with bills",
    "My share of Airbnb",
    "My part of the trip",
)

MEMO_HIGH: tuple[str, ...] = ("My share of rent",)

# Telegram HTML: memo is wrapped in <code> so players can tap to copy.
_DEFAULT_TEMPLATE = (
    "Venmo: {link}\n"
    "\n"
    "• Ensure the payment is for friends and family. Anything else will be refunded\n"
    "\n"
    "• Please put <code>{memo}</code> in the payment caption when sending.\n"
    "\n"
    "• Once sent, please send us a screenshot"
)


def normalize_venmo_tag(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    if s and not s.startswith("@"):
        s = f"@{s}"
    return s


def normalize_venmo_link(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def username_from_venmo_tag(tag: str) -> str:
    return normalize_venmo_tag(tag).lstrip("@")


def username_from_venmo_link(link: str) -> str | None:
    m = _LINK_RE.match(normalize_venmo_link(link))
    if not m:
        return None
    return normalize_venmo_link(link).rsplit("/", 1)[-1]


def validate_venmo_tag(raw: str | None) -> str:
    """Return normalized tag or raise ValueError."""
    tag = normalize_venmo_tag(raw)
    if not _TAG_RE.match(tag):
        raise ValueError("Venmo tag must be @username (2–30 letters, digits, _ or -).")
    return tag


def validate_venmo_link(raw: str | None) -> str:
    """Return normalized link or raise ValueError."""
    link = normalize_venmo_link(raw)
    if not _LINK_RE.match(link):
        raise ValueError(
            "Venmo link must be https://venmo.com/u/username "
            "(https only, no www, query, or trailing slash)."
        )
    return link


def validate_venmo_tag_and_link(
    tag: str | None,
    link: str | None,
) -> tuple[str, str]:
    """Validate both fields and require the same username."""
    norm_tag = validate_venmo_tag(tag)
    norm_link = validate_venmo_link(link)
    if username_from_venmo_tag(norm_tag) != username_from_venmo_link(norm_link):
        raise ValueError("Venmo tag and link must name the same account.")
    return norm_tag, norm_link


def validate_venmo_response_mode(raw: str | None) -> VenmoResponseMode:
    mode = (raw or "").strip().lower()
    if mode not in VENMO_RESPONSE_MODES:
        raise ValueError("Venmo response mode must be default, text, or photo.")
    return mode  # type: ignore[return-value]


def response_type_for_venmo_mode(mode: VenmoResponseMode) -> str:
    if mode == "photo":
        return "photo"
    return "text"


def extract_unique_venmo_link_from_text(*fields: str | None) -> str | None:
    """Return one canonical link if all found usernames agree; else None."""
    found: set[str] = set()
    for field in fields:
        if not field:
            continue
        for m in _LINK_IN_TEXT_RE.finditer(field):
            found.add(m.group(1).lower())
    if len(found) != 1:
        return None
    username = next(iter(found))
    return f"https://venmo.com/u/{username}"


def text_contains_venmo_link(text: str | None, link: str) -> bool:
    """True when ``text`` contains the same username as ``link``."""
    expected = username_from_venmo_link(normalize_venmo_link(link))
    if not expected or not text:
        return False
    for m in _LINK_IN_TEXT_RE.finditer(text):
        if m.group(1).lower() == expected:
            return True
    return False


def pick_venmo_memo(amount: Decimal | float | int | str) -> str:
    """Choose a cover memo from the deposit amount the player entered."""
    value = Decimal(str(amount))
    if value <= Decimal("500"):
        return random.choice(MEMO_LOW)
    if value < Decimal("1000"):
        return random.choice(MEMO_MEDIUM)
    return MEMO_HIGH[0]


def memo_bucket_for_amount(
    amount: Decimal | float | int | str,
) -> Literal["low", "medium", "high"]:
    value = Decimal(str(amount))
    if value <= Decimal("500"):
        return "low"
    if value < Decimal("1000"):
        return "medium"
    return "high"


def build_default_venmo_response_text(
    link: str,
    amount: Decimal | float | int | str,
    *,
    memo: str | None = None,
) -> str:
    """Player-facing default deposit copy (Telegram HTML) with link and memo."""
    norm_link = validate_venmo_link(link)
    phrase = memo if memo is not None else pick_venmo_memo(amount)
    return _DEFAULT_TEMPLATE.format(
        link=html.escape(norm_link),
        memo=html.escape(phrase),
    )


def stored_venmo_tag(variant) -> Optional[str]:
    """Prefer stored tag; else scrape response fields."""
    tag = getattr(variant, "venmo_tag", None)
    if isinstance(tag, str) and tag.strip():
        try:
            return validate_venmo_tag(tag)
        except ValueError:
            pass
    from bot.services.payment_method_binding import extract_venmo_handle_from_text

    for field in (
        getattr(variant, "response_text", None),
        getattr(variant, "response_caption", None),
    ):
        if not isinstance(field, str):
            continue
        handle = extract_venmo_handle_from_text(field)
        if handle:
            return handle
    return None


def stored_venmo_link(variant) -> Optional[str]:
    """Prefer stored link; else scrape response fields."""
    link = getattr(variant, "venmo_link", None)
    if isinstance(link, str) and link.strip():
        try:
            return validate_venmo_link(link)
        except ValueError:
            pass
    from bot.services.payment_method_binding import extract_venmo_url

    for field in (
        getattr(variant, "response_text", None),
        getattr(variant, "response_caption", None),
    ):
        if not isinstance(field, str):
            continue
        url = extract_venmo_url(field)
        if url:
            try:
                return validate_venmo_link(url)
            except ValueError:
                # extract_venmo_url may return a slightly different shape
                username = url.rstrip("/").rsplit("/", 1)[-1].lower()
                if re.fullmatch(r"[a-z0-9_-]{2,30}", username):
                    return f"https://venmo.com/u/{username}"
    return None
