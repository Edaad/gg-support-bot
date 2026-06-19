"""Shared v2 tier/variant helpers for API, migrations, and seeds."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db.models import ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

DEFAULT_VARIANT_LABEL = "Default"
DEFAULT_TIER_LABEL = "Default"

RESPONSE_UPDATE_FIELDS = frozenset(
    {"response_type", "response_text", "response_file_id", "response_caption"}
)

# Open bounds for overlap checks (matches bot tier matching: null min/max = unbounded).
_AMOUNT_LOW = Decimal("-1000000000")
_AMOUNT_HIGH = Decimal("1000000000")


def _bound_low(value: Optional[Decimal]) -> Decimal:
    return value if value is not None else _AMOUNT_LOW


def _bound_high(value: Optional[Decimal]) -> Decimal:
    return value if value is not None else _AMOUNT_HIGH


def amounts_overlap(
    min_a: Optional[Decimal],
    max_a: Optional[Decimal],
    min_b: Optional[Decimal],
    max_b: Optional[Decimal],
) -> bool:
    """True if some deposit amount could match both bands (inclusive bounds)."""
    return _bound_low(min_a) <= _bound_high(max_b) and _bound_low(min_b) <= _bound_high(max_a)


def primary_tier_for_method(siblings: Sequence[ClubPaymentTier]) -> Optional[ClubPaymentTier]:
    ordered = sorted(siblings, key=lambda t: (t.sort_order, t.id))
    if not ordered:
        return None
    return next((t for t in ordered if t.label == DEFAULT_TIER_LABEL), ordered[0])


def is_primary_tier(tier: ClubPaymentTier, siblings: Sequence[ClubPaymentTier]) -> bool:
    primary = primary_tier_for_method(siblings)
    return primary is not None and int(tier.id) == int(primary.id)


def validate_tier_amount_band(
    method: ClubPaymentMethod,
    tier_min: Optional[Decimal],
    tier_max: Optional[Decimal],
    siblings: Sequence[ClubPaymentTier],
    *,
    exclude_tier_id: Optional[int] = None,
    tier_label: Optional[str] = None,
) -> None:
    """Ensure tier band fits method envelope and does not overlap sibling tiers."""
    if tier_min is not None and tier_max is not None and tier_min > tier_max:
        raise HTTPException(400, "Tier min amount cannot be greater than max amount.")

    if tier_min is not None and method.min_amount is not None and tier_min < method.min_amount:
        raise HTTPException(
            400,
            f"Tier min ${tier_min} is below method absolute minimum ${method.min_amount}.",
        )
    if tier_max is not None and method.max_amount is not None and tier_max > method.max_amount:
        raise HTTPException(
            400,
            f"Tier max ${tier_max} is above method absolute maximum ${method.max_amount}.",
        )
    if tier_min is not None and method.max_amount is not None and tier_min > method.max_amount:
        raise HTTPException(
            400,
            f"Tier min ${tier_min} is above method absolute maximum ${method.max_amount}.",
        )
    if tier_max is not None and method.min_amount is not None and tier_max < method.min_amount:
        raise HTTPException(
            400,
            f"Tier max ${tier_max} is below method absolute minimum ${method.min_amount}.",
        )

    for sibling in siblings:
        if exclude_tier_id is not None and sibling.id == exclude_tier_id:
            continue
        if amounts_overlap(tier_min, tier_max, sibling.min_amount, sibling.max_amount):
            sib_min = sibling.min_amount
            sib_max = sibling.max_amount
            band = sibling.label or f"tier {sibling.id}"
            raise HTTPException(
                400,
                f"Amount band overlaps with {band!r} "
                f"(${sib_min if sib_min is not None else '—'}–"
                f"${sib_max if sib_max is not None else '—'}).",
            )


def validate_all_method_tiers(method: ClubPaymentMethod) -> None:
    """Re-validate every tier on a method (e.g. after method envelope changes)."""
    siblings = list(method.tiers or [])
    for tier in siblings:
        validate_tier_amount_band(
            method,
            tier.min_amount,
            tier.max_amount,
            siblings,
            exclude_tier_id=tier.id,
            tier_label=tier.label,
        )


def validate_checkout_amount_bounds(
    method: ClubPaymentMethod,
    checkout_min: Optional[Decimal],
    checkout_max: Optional[Decimal],
) -> None:
    """Ensure optional checkout min/max fit the method absolute envelope."""
    if checkout_min is not None and checkout_max is not None and checkout_min > checkout_max:
        raise HTTPException(400, "Checkout min cannot be greater than checkout max.")

    if checkout_min is not None and method.min_amount is not None and checkout_min < method.min_amount:
        raise HTTPException(
            400,
            f"Checkout min ${checkout_min} is below method absolute minimum ${method.min_amount}.",
        )
    if checkout_max is not None and method.max_amount is not None and checkout_max > method.max_amount:
        raise HTTPException(
            400,
            f"Checkout max ${checkout_max} is above method absolute maximum ${method.max_amount}.",
        )
    if checkout_min is not None and method.max_amount is not None and checkout_min > method.max_amount:
        raise HTTPException(
            400,
            f"Checkout min ${checkout_min} is above method absolute maximum ${method.max_amount}.",
        )
    if checkout_max is not None and method.min_amount is not None and checkout_max < method.min_amount:
        raise HTTPException(
            400,
            f"Checkout max ${checkout_max} is below method absolute minimum ${method.min_amount}.",
        )


def clamp_checkout_amount_bounds(
    method: ClubPaymentMethod,
    checkout_min: Optional[Decimal],
    checkout_max: Optional[Decimal],
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Clamp checkout bounds into the method absolute envelope (null = inherit / unbounded)."""
    lo = checkout_min
    hi = checkout_max

    if method.min_amount is not None:
        if lo is not None and lo < method.min_amount:
            lo = method.min_amount
        if hi is not None and hi < method.min_amount:
            hi = method.min_amount

    if method.max_amount is not None:
        if lo is not None and lo > method.max_amount:
            lo = method.max_amount
        if hi is not None and hi > method.max_amount:
            hi = method.max_amount

    if lo is not None and hi is not None and lo > hi:
        hi = lo

    return lo, hi


def effective_tier_checkout_min(tier: ClubPaymentTier) -> Optional[Decimal]:
    if tier.checkout_min_amount is not None:
        return tier.checkout_min_amount
    return tier.min_amount


def effective_tier_checkout_max(tier: ClubPaymentTier) -> Optional[Decimal]:
    if tier.checkout_max_amount is not None:
        return tier.checkout_max_amount
    return tier.max_amount


def _inherited_checkout_value(
    current: Optional[Decimal],
    prior_effective: Optional[Decimal],
) -> bool:
    """True when a checkout bound was unset or matched the tier's previous effective value."""
    if current is None:
        return True
    if prior_effective is not None and current == prior_effective:
        return True
    return False


def sync_tier_checkout_bounds_from_band(
    tier: ClubPaymentTier,
    *,
    prior_min: Optional[Decimal],
    prior_max: Optional[Decimal],
    prior_checkout_min: Optional[Decimal],
    prior_checkout_max: Optional[Decimal],
    lock_checkout_min: bool = False,
) -> None:
    """Align tier checkout defaults when deposit band or checkout fields change."""
    prior_eff_min = prior_checkout_min if prior_checkout_min is not None else prior_min
    prior_eff_max = prior_checkout_max if prior_checkout_max is not None else prior_max

    if not lock_checkout_min:
        if _inherited_checkout_value(tier.checkout_min_amount, prior_eff_min):
            tier.checkout_min_amount = tier.min_amount
    if _inherited_checkout_value(tier.checkout_max_amount, prior_eff_max):
        tier.checkout_max_amount = tier.max_amount


def sync_tier_checkout_bounds_to_variants(
    tier: ClubPaymentTier,
    method: ClubPaymentMethod,
    *,
    prior_min: Optional[Decimal],
    prior_max: Optional[Decimal],
    prior_checkout_min: Optional[Decimal],
    prior_checkout_max: Optional[Decimal],
    lock_variant_checkout_min: bool = False,
) -> None:
    """Push tier checkout envelope to variants that inherited prior tier values."""
    prior_eff_min = prior_checkout_min if prior_checkout_min is not None else prior_min
    prior_eff_max = prior_checkout_max if prior_checkout_max is not None else prior_max
    new_eff_min = effective_tier_checkout_min(tier)
    new_eff_max = effective_tier_checkout_max(tier)

    for variant in tier.variants or []:
        if not lock_variant_checkout_min:
            if _inherited_checkout_value(variant.checkout_min_amount, prior_eff_min):
                variant.checkout_min_amount = new_eff_min
        if _inherited_checkout_value(variant.checkout_max_amount, prior_eff_max):
            variant.checkout_max_amount = new_eff_max

        variant.checkout_min_amount, variant.checkout_max_amount = clamp_checkout_amount_bounds(
            method,
            variant.checkout_min_amount,
            variant.checkout_max_amount,
        )


def sync_method_envelope_side_effects(method: ClubPaymentMethod) -> None:
    """Clamp tier/variant checkout bounds when method absolute envelope changes."""
    for tier in method.tiers or []:
        tier.checkout_min_amount, tier.checkout_max_amount = clamp_checkout_amount_bounds(
            method,
            tier.checkout_min_amount,
            tier.checkout_max_amount,
        )
        for variant in tier.variants or []:
            variant.checkout_min_amount, variant.checkout_max_amount = clamp_checkout_amount_bounds(
                method,
                variant.checkout_min_amount,
                variant.checkout_max_amount,
            )


_FIRST_TIME_BIND_SLUGS = frozenset({"venmo", "zelle", "cashapp", "paypal"})
_FIRST_TIME_BIND_MODES = frozenset({"special_amount", "memo_emoji"})


def validate_first_time_linking(method: ClubPaymentMethod) -> None:
    """Raise ValueError when first-time linking settings are inconsistent."""
    slug = (method.slug or "").strip().lower()
    enabled = bool(getattr(method, "first_time_linking_enabled", False))
    mode = (getattr(method, "first_time_bind_mode", None) or "").strip().lower() or None
    if not enabled:
        return
    if method.direction != "deposit":
        raise ValueError("First-time linking applies to deposit methods only.")
    if slug not in _FIRST_TIME_BIND_SLUGS:
        raise ValueError(
            "First-time linking is only supported for venmo, zelle, cashapp, and paypal."
        )
    if slug == "zelle" and mode == "memo_emoji":
        raise ValueError("Memo first-time linking is not supported for Zelle.")
    if mode not in _FIRST_TIME_BIND_MODES:
        raise ValueError("Select a verification method when first-time linking is enabled.")


def method_needs_variants(method: ClubPaymentMethod) -> bool:
    return not method.has_sub_options


def tier_has_response(tier: ClubPaymentTier) -> bool:
    rt = (tier.response_type or "text").strip().lower()
    if rt in ("file", "photo", "video"):
        return bool((tier.response_file_id or "").strip())
    return bool((tier.response_text or "").strip())


def clear_tier_response(tier: ClubPaymentTier) -> None:
    tier.response_type = "text"
    tier.response_text = None
    tier.response_file_id = None
    tier.response_caption = None


def tier_variant_count(session: Session, tier_id: int) -> int:
    return session.query(ClubPaymentTierVariant).filter_by(tier_id=tier_id).count()


def tier_has_default_variant(session: Session, tier_id: int) -> bool:
    return (
        session.query(ClubPaymentTierVariant)
        .filter_by(tier_id=tier_id, label=DEFAULT_VARIANT_LABEL)
        .first()
        is not None
    )


def create_default_variant_from_tier(
    session: Session,
    tier: ClubPaymentTier,
    *,
    label: str = DEFAULT_VARIANT_LABEL,
    weight: int = 1,
    clear_tier: bool = True,
) -> ClubPaymentTierVariant:
    variant = ClubPaymentTierVariant(
        method_id=tier.method_id,
        tier_id=tier.id,
        label=label,
        weight=weight,
        sort_order=0,
        response_type=tier.response_type or "text",
        response_text=tier.response_text,
        response_file_id=tier.response_file_id,
        response_caption=tier.response_caption,
        use_group_checkout_link=True if tier.use_group_checkout_link else None,
        group_checkout_provider=tier.group_checkout_provider if tier.use_group_checkout_link else None,
        hyperlink_text=tier.hyperlink_text if tier.use_group_checkout_link else None,
        checkout_min_amount=tier.checkout_min_amount,
        checkout_max_amount=tier.checkout_max_amount,
    )
    session.add(variant)
    session.flush()
    if clear_tier:
        clear_tier_response(tier)
        session.flush()
    return variant


def create_empty_default_variant(
    session: Session,
    tier: ClubPaymentTier,
    *,
    label: str = DEFAULT_VARIANT_LABEL,
) -> ClubPaymentTierVariant:
    variant = ClubPaymentTierVariant(
        method_id=tier.method_id,
        tier_id=tier.id,
        label=label,
        weight=1,
        sort_order=0,
        response_type="text",
        response_text=None,
        response_file_id=None,
        response_caption=None,
        use_group_checkout_link=None,
        group_checkout_provider=None,
        hyperlink_text=None,
        checkout_min_amount=None,
        checkout_max_amount=None,
    )
    session.add(variant)
    session.flush()
    return variant


def migrate_legacy_tier_response_to_variant(
    session: Session,
    tier: ClubPaymentTier,
) -> Optional[ClubPaymentTierVariant]:
    """If tier has legacy response and no variants, copy to Default variant."""
    if tier_variant_count(session, tier.id) > 0:
        return None
    if not tier_has_response(tier):
        return None
    if tier_has_default_variant(session, tier.id):
        return None
    return create_default_variant_from_tier(session, tier)


def ensure_legacy_tier_before_new_variant(
    session: Session,
    tier: ClubPaymentTier,
) -> Optional[ClubPaymentTierVariant]:
    """Migration safety: preserve tier copy as Default before adding another variant."""
    return migrate_legacy_tier_response_to_variant(session, tier)


def strip_response_from_tier_payload(data: dict) -> dict:
    out = dict(data)
    for field in RESPONSE_UPDATE_FIELDS:
        out.pop(field, None)
    return out


def upsert_default_variant_for_tier(
    session: Session,
    tier: ClubPaymentTier,
    *,
    response_text: str | None,
    response_type: str = "text",
    use_group_checkout_link: bool | None = None,
    group_checkout_provider: str | None = None,
    hyperlink_text: str | None = None,
    checkout_min_amount=None,
    checkout_max_amount=None,
) -> ClubPaymentTierVariant:
    variant = (
        session.query(ClubPaymentTierVariant)
        .filter_by(tier_id=tier.id, label=DEFAULT_VARIANT_LABEL)
        .first()
    )
    if variant is None:
        variant = ClubPaymentTierVariant(
            method_id=tier.method_id,
            tier_id=tier.id,
            label=DEFAULT_VARIANT_LABEL,
        )
        session.add(variant)

    variant.weight = 1
    variant.sort_order = 0
    variant.response_type = response_type
    variant.response_text = response_text
    variant.response_file_id = None
    variant.response_caption = None
    variant.use_group_checkout_link = use_group_checkout_link
    variant.group_checkout_provider = group_checkout_provider
    variant.hyperlink_text = hyperlink_text
    variant.checkout_min_amount = checkout_min_amount
    variant.checkout_max_amount = checkout_max_amount
    clear_tier_response(tier)
    session.flush()
    return variant
