"""Resolve payment method display name and slug for cashier jobs."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.services.club import (
    get_method_by_id,
    get_sub_option_by_id,
    get_tier_for_amount,
    pick_variant,
)


def resolve_method_display(
    method_id: int,
    amount: Decimal,
    sub_option_id: Optional[int] = None,
) -> tuple[str, str]:
    """Return (method_display_name, slug) for a chosen method."""
    method = get_method_by_id(method_id)
    if not method:
        return ("Unknown", "other")

    slug = method.get("slug") or "other"
    display_name = method["name"]

    if sub_option_id is not None:
        sub = get_sub_option_by_id(sub_option_id)
        if sub:
            display_name = f"{method['name']} — {sub['name']}"
            slug = sub.get("slug") or slug

    return (display_name, slug)
