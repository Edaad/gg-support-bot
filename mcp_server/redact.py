"""PII masking and secret removal applied to every tool result.

Secrets are dropped at any depth regardless of flags. PII is masked unless the tool
was called with ``include_pii=true``. Navigation keys (``chat_id``,
``telegram_user_id``, ``gg_player_id``, ``group_title``) stay visible because every
dashboard lookup pivots on them; ``group_title`` embeds a player name, which is
documented in the ``gg://reference/conventions`` resource.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REDACTED = "[redacted]"

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "access_token_encrypted",
        "telethon_auth_string",
        "pushover_user_key",
        "content",
        "request_body",
        "response_json",
        "raw_payload",
        "raw_body",
        "agent_name",
        "sling_user_id",
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
    }
)

NAME_KEYS: frozenset[str] = frozenset(
    {
        "payer_name",
        "payer_name_normalized",
        "sender_name",
        "recipient",
        "player_display_name",
        "display_name",
        "player_name",
        "counterparty_name",
        "merchant_name",
        "from_entity_name",
        "reporter_name",
        "customer_name",
        "name_on_account",
        "payment_account_name",
        "sender_display_name",
    }
)
EMAIL_KEYS: frozenset[str] = frozenset({"paypal_email", "email", "customer_email"})
HANDLE_KEYS: frozenset[str] = frozenset(
    {
        "venmo_handle",
        "cashapp_handle",
        "zelle_recipient",
        "handle",
        "username",
        "player_username",
        "payer_handle",
        "tag",
        "venmo_tag",
        "cashapp_tag",
        "destination_tag",
    }
)
FREE_TEXT_KEYS: frozenset[str] = frozenset(
    {
        "memo",
        "note_from_payer",
        "payout_details",
        "payout_detail",
        "original_description",
        "payer_summary",
        "invite_link",
        "link",
        "venmo_link",
        "cashapp_link",
        "proof_link",
    }
)
PHONE_KEYS: frozenset[str] = frozenset({"phone", "phone_number"})
CRYPTO_KEYS: frozenset[str] = frozenset(
    {
        "from_address",
        "to_address",
        "address",
        "crypto_address",
        "transaction_hash",
        "tx_hash",
    }
)

PII_KEYS: frozenset[str] = (
    NAME_KEYS | EMAIL_KEYS | HANDLE_KEYS | FREE_TEXT_KEYS | PHONE_KEYS | CRYPTO_KEYS
)


def _mask_name(value: str) -> str:
    text = value.strip()
    return (text[:1] + "***") if text else text


def _mask_email(value: str) -> str:
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_name(value)
    return f"{local[:2]}***@{domain}"


def _mask_handle(value: str) -> str:
    text = value.strip().lstrip("@")
    return "@" + text[:2] + "***" if text else value


def _mask_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"***{digits[-4:]}" if digits else REDACTED


def _mask_crypto(value: str) -> str:
    text = value.strip()
    if len(text) <= 12:
        return REDACTED
    return f"{text[:6]}…{text[-4:]}"


def mask(key: str, value: Any) -> Any:
    """Mask a single PII value by key; non-string values are redacted wholesale."""
    if value is None:
        return None
    if not isinstance(value, str):
        return REDACTED
    if key in NAME_KEYS:
        return _mask_name(value)
    if key in EMAIL_KEYS:
        return _mask_email(value)
    if key in HANDLE_KEYS:
        return _mask_handle(value)
    if key in PHONE_KEYS:
        return _mask_phone(value)
    if key in CRYPTO_KEYS:
        return _mask_crypto(value)
    return REDACTED


def redact(obj: Any, *, include_pii: bool) -> Any:
    """Recursively drop secrets and (unless ``include_pii``) mask PII keys."""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for raw_key, value in obj.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in SECRET_KEYS:
                continue
            if not include_pii and lowered in PII_KEYS:
                out[key] = mask(lowered, value)
                continue
            out[key] = redact(value, include_pii=include_pii)
        return out
    if isinstance(obj, list):
        return [redact(v, include_pii=include_pii) for v in obj]
    if isinstance(obj, tuple):
        return [redact(v, include_pii=include_pii) for v in obj]
    return obj
