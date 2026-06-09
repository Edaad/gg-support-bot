"""Read deposit/cashout config from greenfield club_payment_* tables.

Used by default (BOT_USE_PAYMENT_V2 defaults to on; set 0 for legacy payment_*).
Returns the same dict shapes as bot.services.club legacy helpers.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import List, Optional

from db.connection import get_db
from db.models import (
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
    ClubPaymentTierVariant,
)


def _method_dict(m: ClubPaymentMethod) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "slug": m.slug,
        "min_amount": m.min_amount,
        "max_amount": m.max_amount,
        "has_sub_options": m.has_sub_options,
        "response_type": None,
        "response_text": None,
        "response_file_id": None,
        "response_caption": None,
        "use_group_checkout_link": False,
        "group_checkout_provider": None,
        "hyperlink_text": None,
    }


def _tier_dict(t: ClubPaymentTier) -> dict:
    link = bool(t.use_group_checkout_link)
    provider = t.group_checkout_provider
    if link and not provider:
        provider = "stripe"
    return {
        "id": t.id,
        "label": t.label,
        "min_amount": t.min_amount,
        "max_amount": t.max_amount,
        "checkout_min_amount": t.checkout_min_amount,
        "checkout_max_amount": t.checkout_max_amount,
        "response_type": t.response_type,
        "response_text": t.response_text,
        "response_file_id": t.response_file_id,
        "response_caption": t.response_caption,
        "use_group_checkout_link": link,
        "group_checkout_provider": provider,
        "hyperlink_text": t.hyperlink_text,
    }


def _variant_response_dict(v: ClubPaymentTierVariant, *, include_ids: bool = False) -> dict:
    link = v.use_group_checkout_link
    provider = v.group_checkout_provider
    if link is True and not provider:
        provider = "stripe"
    data = {
        "response_type": v.response_type,
        "response_text": v.response_text,
        "response_file_id": v.response_file_id,
        "response_caption": v.response_caption,
        "hyperlink_text": v.hyperlink_text,
        "checkout_min_amount": v.checkout_min_amount,
        "checkout_max_amount": v.checkout_max_amount,
    }
    if link is not None:
        data["use_group_checkout_link"] = bool(link)
    if provider and data.get("use_group_checkout_link"):
        data["group_checkout_provider"] = provider
    if include_ids:
        data["variant_id"] = int(v.id)
        data["variant_label"] = v.label
        data["tier_id"] = int(v.tier_id) if v.tier_id else None
        data["method_id"] = int(v.method_id)
    return data


def get_methods_for_amount(
    club_id: int, direction: str, amount: Optional[Decimal] = None
) -> List[dict]:
    with get_db() as session:
        methods = (
            session.query(ClubPaymentMethod)
            .filter_by(club_id=club_id, direction=direction, is_active=True)
            .order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id)
            .all()
        )
        result = []
        for m in methods:
            if m.deposit_limit is not None and m.accumulated_amount is not None:
                if m.accumulated_amount >= m.deposit_limit:
                    continue
            if amount is not None:
                if m.min_amount is not None and amount < m.min_amount:
                    continue
                if m.max_amount is not None and amount > m.max_amount:
                    continue
            result.append(_method_dict(m))
        return result


def get_method_by_id(method_id: int) -> Optional[dict]:
    with get_db() as session:
        m = session.query(ClubPaymentMethod).get(method_id)
        if not m:
            return None
        return _method_dict(m)


def get_sub_options(method_id: int) -> List[dict]:
    with get_db() as session:
        subs = (
            session.query(ClubPaymentSubOption)
            .filter_by(method_id=method_id, is_active=True)
            .order_by(ClubPaymentSubOption.sort_order, ClubPaymentSubOption.id)
            .all()
        )
        return [
            {
                "id": s.id,
                "name": s.name,
                "slug": s.slug,
                "response_type": s.response_type,
                "response_text": s.response_text,
                "response_file_id": s.response_file_id,
                "response_caption": s.response_caption,
            }
            for s in subs
        ]


def get_sub_option_by_id(sub_id: int) -> Optional[dict]:
    with get_db() as session:
        s = session.query(ClubPaymentSubOption).get(sub_id)
        if not s:
            return None
        return {
            "id": s.id,
            "name": s.name,
            "slug": s.slug,
            "response_type": s.response_type,
            "response_text": s.response_text,
            "response_file_id": s.response_file_id,
            "response_caption": s.response_caption,
        }


def get_lowest_minimum(club_id: int, direction: str) -> Optional[Decimal]:
    with get_db() as session:
        methods = (
            session.query(ClubPaymentMethod)
            .filter_by(club_id=club_id, direction=direction, is_active=True)
            .all()
        )
        mins = [m.min_amount for m in methods if m.min_amount is not None]
        return min(mins) if mins else None


def get_tier_for_amount(method_id: int, amount: Decimal) -> Optional[dict]:
    with get_db() as session:
        tiers = (
            session.query(ClubPaymentTier)
            .filter_by(method_id=method_id)
            .order_by(ClubPaymentTier.sort_order, ClubPaymentTier.id)
            .all()
        )
        for t in tiers:
            if t.min_amount is not None and amount < t.min_amount:
                continue
            if t.max_amount is not None and amount > t.max_amount:
                continue
            return _tier_dict(t)
    return None


def list_tier_variants(method_id: int, tier_id: int) -> list[dict]:
    """Return tier variants as response dicts with weight, ordered like pick_variant."""
    with get_db() as session:
        variants = (
            session.query(ClubPaymentTierVariant)
            .filter_by(method_id=int(method_id), tier_id=int(tier_id))
            .order_by(ClubPaymentTierVariant.sort_order, ClubPaymentTierVariant.id)
            .all()
        )
        out: list[dict] = []
        for variant in variants:
            data = _variant_response_dict(variant, include_ids=True)
            data["weight"] = int(variant.weight or 1)
            out.append(data)
        return out


def pick_variant(
    method_id: int,
    tier_id: Optional[int] = None,
    *,
    variant_id: Optional[int] = None,
) -> Optional[dict]:
    with get_db() as session:
        if variant_id is not None:
            chosen = session.query(ClubPaymentTierVariant).get(int(variant_id))
            if chosen is None or int(chosen.method_id) != int(method_id):
                return None
            if tier_id is None or int(chosen.tier_id) == int(tier_id):
                return _variant_response_dict(chosen, include_ids=True)

        if tier_id is not None:
            variants = (
                session.query(ClubPaymentTierVariant)
                .filter_by(tier_id=tier_id)
                .order_by(ClubPaymentTierVariant.sort_order, ClubPaymentTierVariant.id)
                .all()
            )
            if variants:
                weights = [v.weight for v in variants]
                chosen = random.choices(variants, weights=weights, k=1)[0]
                return _variant_response_dict(chosen, include_ids=True)
        return None


def record_method_deposit(method_id: int, amount: Decimal) -> None:
    with get_db() as session:
        m = session.query(ClubPaymentMethod).get(method_id)
        if m:
            m.accumulated_amount = (m.accumulated_amount or 0) + amount
