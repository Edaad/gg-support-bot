"""Validate a player-supplied payout handle for automated cashouts.

Each cashout method only accepts a specific kind of destination:

    venmo   -> @handle or a venmo.com link
    cashapp -> $cashtag or a cash.app link
    zelle   -> a US phone number or an email
    crypto  -> a wallet-address-looking token
    paypal  -> an email or a paypal.me link

``validate_cashout_handle(slug, text)`` searches the player's message for a
valid destination and returns it normalized, or ``None`` when nothing valid is
present (the caller escalates). Extraction (rather than full-match) keeps the
flow low-friction: "my venmo is @john" still works.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

_VENMO_URL_RE = re.compile(
    r"https?://(?:www\.)?venmo\.com/(?:u/)?([A-Za-z0-9_.-]{2,30})",
    re.IGNORECASE,
)
_VENMO_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.-]{2,30})")

_CASHAPP_URL_RE = re.compile(
    r"https?://(?:www\.)?cash\.app/\$?([A-Za-z0-9_-]{1,30})",
    re.IGNORECASE,
)
_CASHAPP_TAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z0-9_-]{1,30})(?![A-Za-z0-9])")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_US_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

_PAYPAL_ME_RE = re.compile(
    r"https?://(?:www\.)?paypal\.me/([A-Za-z0-9_.-]{2,40})",
    re.IGNORECASE,
)

# Wallet-address-looking token: a single long alphanumeric run. Long enough that
# ordinary words never match; case is preserved (crypto addresses are
# case-sensitive).
_CRYPTO_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{24,120}(?![A-Za-z0-9])")


def _validate_venmo(text: str) -> Optional[str]:
    m = _VENMO_URL_RE.search(text) or _VENMO_HANDLE_RE.search(text)
    if not m:
        return None
    return f"@{m.group(1).lstrip('@')}".lower()


def _validate_cashapp(text: str) -> Optional[str]:
    m = _CASHAPP_URL_RE.search(text) or _CASHAPP_TAG_RE.search(text)
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
    email = _EMAIL_RE.search(text)
    if email:
        return email.group(0).lower()
    link = _PAYPAL_ME_RE.search(text)
    if link:
        return f"paypal.me/{link.group(1)}"
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
