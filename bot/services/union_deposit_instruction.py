"""Build player-facing Telegram instructions for union deposit methods."""

from __future__ import annotations

import html
from decimal import Decimal
from typing import Any, Optional

from bot.services.union_method_types import union_type_display_name, validate_union_method_type

_FOOTER_LINES = (
    "Please put a random emoji in the payment caption when sending",
    "Credits will be added as soon as we receive them.",
)
_TAG_COPY_HINT = "Tap the tag below to copy it."


def _read_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _format_usd(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return f"${int(q)}"
    return f"${q:.2f}"


def _read_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _method_union_type(method: Any) -> Optional[str]:
    raw = _read_attr(method, "union_type")
    if raw:
        try:
            return validate_union_method_type(str(raw))
        except ValueError:
            return None
    return None


def build_union_deposit_instruction(
    method: Any,
    *,
    used_sum: Decimal,
    html_mode: bool = False,
) -> Optional[str]:
    """Return instruction text, or None if method is not fully configured."""
    type_slug = _method_union_type(method)
    method_tag = (_read_attr(method, "method_tag") or "").strip()
    if not type_slug or not method_tag:
        return None

    deposit_limit = _read_decimal(_read_attr(method, "deposit_limit"))
    if deposit_limit is None or deposit_limit <= 0:
        return None

    used = Decimal(str(used_sum))
    remaining = deposit_limit - used
    if remaining <= 0:
        remaining = Decimal("0")

    max_cap = _read_decimal(_read_attr(method, "max_amount"))
    max_display = remaining if max_cap is None else min(remaining, max_cap)

    lines: list[str] = []
    min_amount = _read_decimal(_read_attr(method, "min_amount"))
    if min_amount is not None:
        lines.append(f"Min: {_format_usd(min_amount)}")
    lines.append(f"Max: {_format_usd(max_display)}")
    lines.append("")

    type_label = union_type_display_name(type_slug)
    if html_mode:
        lines.append(f"{html.escape(type_label)} Tag:")
        lines.append(_TAG_COPY_HINT)
        lines.append("")
        lines.append(f"<code>{html.escape(method_tag)}</code>")
    else:
        lines.append(f"{type_label} Tag: {method_tag}")

    account_name = (_read_attr(method, "payment_account_name") or "").strip()
    if account_name:
        if html_mode:
            lines.append(f"{html.escape(type_label)} Name: {html.escape(account_name)}")
        else:
            lines.append(f"{type_label} Name: {account_name}")

    lines.append("")
    lines.extend(_FOOTER_LINES)
    return "\n".join(lines)


def build_union_deposit_instruction_from_dict(
    method: dict,
    *,
    used_sum: Decimal,
) -> Optional[str]:
    """Dict wrapper for bot deposit handler."""
    return build_union_deposit_instruction(method, used_sum=used_sum)
