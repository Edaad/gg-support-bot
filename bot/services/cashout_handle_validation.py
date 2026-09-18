"""Validate a player-supplied payout handle for automated cashouts.

Each cashout method only accepts a specific kind of destination:

    venmo   -> @handle or a venmo.com link
    cashapp -> $cashtag or a cash.app link
    zelle   -> a US phone number or an email
    crypto  -> a wallet-address-looking token
    paypal  -> an email or a paypal.me / paypal.com/paypalme link

``validate_cashout_handle(slug, text)`` searches the player's message for a
valid destination and returns it normalized, or ``None`` when nothing valid is
present (the caller escalates). Extraction (rather than full-match) keeps the
flow low-friction: "my venmo is @john" still works.

A link is recorded as a link, never boiled down to the handle inside it: the
dashboard turns a payout that starts with a scheme into a clickable link, and
one tap beats retyping a handle into an app. A handle is likewise recorded
whole. So whichever form the player sends is the form the payer sees.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Link patterns match the whole URL, including any subdomain, path and query, so
# that the recorded destination is exactly the link the player sent. The leading
# lookbehind keeps ``fakevenmo.com/john`` from matching as ``venmo.com/john``.
_LINK_PREFIX = r"(?<![A-Za-z0-9.-])(?:https?://)?(?:[A-Za-z0-9-]+\.)*"
# A real destination has a path; ``venmo.com/?x`` does not.
_LINK_PATH = r"/[A-Za-z0-9$@_-]\S*"

_VENMO_URL_RE = re.compile(_LINK_PREFIX + r"venmo\.com" + _LINK_PATH, re.IGNORECASE)
# Skip an ``@`` that is part of an email (``john@gmail.com``) — that is not a Venmo
# handle, and silently recording ``@gmail.com`` would mis-pay the player.
_VENMO_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9._%+-])@([A-Za-z0-9_.-]{2,30})")

_CASHAPP_URL_RE = re.compile(_LINK_PREFIX + r"cash\.app" + _LINK_PATH, re.IGNORECASE)
_CASHAPP_TAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z0-9_-]{1,30})(?![A-Za-z0-9])")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_US_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

# Both the legacy paypal.me short link and the share link PayPal hands out today.
_PAYPAL_URL_RE = re.compile(
    _LINK_PREFIX + r"(?:paypal\.me|paypal\.com/paypalme)" + _LINK_PATH,
    re.IGNORECASE,
)

# Wallet-address-looking token: a single long alphanumeric run. Long enough that
# ordinary words never match; case is preserved (crypto addresses are
# case-sensitive).
_CRYPTO_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{24,120}(?![A-Za-z0-9])")


def _link(match: Optional[re.Match]) -> Optional[str]:
    """The matched link, trimmed of sentence punctuation and given a scheme.

    The scheme is what makes the payout clickable on the dashboard; case is left
    alone because a URL path can be case-sensitive.
    """
    if match is None:
        return None
    url = match.group(0).rstrip(".,;:!?)]}>\"'")
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _validate_venmo(text: str) -> Optional[str]:
    link = _link(_VENMO_URL_RE.search(text))
    if link:
        return link
    m = _VENMO_HANDLE_RE.search(text)
    if not m:
        return None
    return f"@{m.group(1).lstrip('@')}".lower()


def _validate_cashapp(text: str) -> Optional[str]:
    link = _link(_CASHAPP_URL_RE.search(text))
    if link:
        return link
    m = _CASHAPP_TAG_RE.search(text)
    if not m:
        return None
    return f"${m.group(1).lstrip('$')}".lower()


def _validate_zelle(text: str) -> Optional[str]:
    email = _EMAIL_RE.search(text)
    if email:
        return email.group(0).lower()
    phone = _US_PHONE_RE.search(text)
    if phone:
        digits = re.sub(r"\D", "", phone.group(0))
        return digits or None
    return None


def _validate_paypal(text: str) -> Optional[str]:
    # Link first: when a player sends both, the link is the easier one to pay.
    link = _link(_PAYPAL_URL_RE.search(text))
    if link:
        return link
    email = _EMAIL_RE.search(text)
    if email:
        return email.group(0).lower()
    return None


def _validate_crypto(text: str) -> Optional[str]:
    # Reject if the message reads like a sentence around the address; still allow
    # a label like "BTC: <addr>". We look for one address-looking token.
    m = _CRYPTO_TOKEN_RE.search(text)
    if not m:
        return None
    return m.group(0)


_VALIDATORS: dict[str, Callable[[str], Optional[str]]] = {
    "venmo": _validate_venmo,
    "cashapp": _validate_cashapp,
    "zelle": _validate_zelle,
    "crypto": _validate_crypto,
    "paypal": _validate_paypal,
}


def supported_cashout_slug(slug: str | None) -> bool:
    """True when automated handle validation exists for this method slug."""
    return (slug or "").strip().lower() in _VALIDATORS


def validate_cashout_handle(slug: str | None, text: str | None) -> Optional[str]:
    """Return the normalized payout handle in ``text`` for ``slug``, or ``None``.

    ``None`` means the message did not contain a valid destination for the method
    and the caller should escalate.
    """
    validator = _VALIDATORS.get((slug or "").strip().lower())
    if validator is None:
        return None
    if not text or not text.strip():
        return None
    return validator(text)
