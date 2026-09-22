"""Tier precedence shared by the bot and the v2 config API.

The default tier is a fallback: it covers the method envelope and is only used
when no specific tier matches the amount. Specific tiers are therefore allowed
to sit inside the default band, and are always tried first.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence, TypeVar

DEFAULT_TIER_LABEL = "Default"

T = TypeVar("T")


def _sort_key(tier) -> tuple:
    return (getattr(tier, "sort_order", 0) or 0, int(tier.id))


def _label(tier) -> str:
    return (getattr(tier, "label", None) or "").strip()


def primary_tier(tiers: Sequence[T]) -> Optional[T]:
    """The tier whose minimum tracks the method minimum: 'Default', else the oldest."""
    ordered = sorted(tiers, key=_sort_key)
    if not ordered:
        return None
    return next((t for t in ordered if _label(t) == DEFAULT_TIER_LABEL), ordered[0])


def is_primary_tier(tier, siblings: Sequence) -> bool:
    primary = primary_tier(siblings)
    return primary is not None and int(tier.id) == int(primary.id)


def fallback_tier(tiers: Sequence[T]) -> Optional[T]:
    """The catch-all tier, which only exists when a tier is labelled 'Default'.

    Methods with explicit disjoint bands (for example 'Under $100' / 'Over
    $100') have no fallback, so their tiers stay mutually exclusive.
    """
    ordered = sorted(tiers, key=_sort_key)
    return next((t for t in ordered if _label(t) == DEFAULT_TIER_LABEL), None)


def tiers_in_match_order(tiers: Sequence[T]) -> list[T]:
    """Specific tiers first (by sort order), fallback tier last."""
    ordered = sorted(tiers, key=_sort_key)
    fallback = fallback_tier(ordered)
    if fallback is None:
        return ordered
    specific = [t for t in ordered if int(t.id) != int(fallback.id)]
    return specific + [fallback]


def tier_band_contains(tier, amount: Decimal) -> bool:
    if tier.min_amount is not None and amount < Decimal(str(tier.min_amount)):
        return False
    if tier.max_amount is not None and amount > Decimal(str(tier.max_amount)):
        return False
    return True


def select_tier_for_amount(tiers: Sequence[T], amount: Decimal) -> Optional[T]:
    """First tier whose band contains the amount, fallback tier considered last."""
    amt = Decimal(str(amount))
    for tier in tiers_in_match_order(tiers):
        if tier_band_contains(tier, amt):
            return tier
    return None
