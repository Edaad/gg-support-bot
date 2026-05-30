"""Copy legacy payment_methods defaults onto amount tiers (non-destructive).

The old dashboard stored player response text, Stripe checkout flags, and rotation
variants on payment_methods (Details tab). The new UI uses amount tiers with nested
variants. This script copies that legacy data into tiers so the dashboard works, while
**keeping** method-level columns and method-level variants (tier_id IS NULL) intact so
the live bot can still fall back to Details until you fully cut over.

Run once with DATABASE_URL set:
  python export_payment_config_backup.py          # recommended before migration
  python migrate_legacy_method_to_tiers.py
  python migrate_legacy_method_to_tiers.py --dry-run

Idempotent: skips tiers that already have matching content; never deletes method rows.
"""

from __future__ import annotations

import argparse
import re
from typing import Iterable, Optional

from dotenv import load_dotenv

load_dotenv()

from db.connection import get_db
from db.models import MethodVariant, PaymentMethod, PaymentMethodTier

DEFAULT_TIER_LABEL = "Default"

ROTATION_VARIANT_LABELS = frozenset({"cashapp stripe", "cashapp account 1"})
ROTATION_LABEL_RE = re.compile(r"^cashapp (stripe|account)", re.I)


def _sort_tiers(tiers: list[PaymentMethodTier]) -> list[PaymentMethodTier]:
    return sorted(tiers, key=lambda t: (t.sort_order or 0, t.id))


def _tier_has_response(tier: PaymentMethodTier) -> bool:
    rt = (tier.response_type or "text").lower()
    if rt in ("file", "photo", "video"):
        return bool((tier.response_file_id or "").strip())
    return bool((tier.response_text or "").strip())


def _method_has_response(method: PaymentMethod) -> bool:
    rt = (method.response_type or "text").lower()
    if rt in ("file", "photo", "video"):
        return bool((method.response_file_id or "").strip())
    return bool((method.response_text or "").strip())


def _primary_tier(
    tiers: list[PaymentMethodTier], method: PaymentMethod
) -> Optional[PaymentMethodTier]:
    sorted_tiers = _sort_tiers(tiers)
    if not sorted_tiers:
        return None

    method_min = method.min_amount
    min_matches = [t for t in sorted_tiers if t.min_amount == method_min]
    if len(min_matches) == 1:
        return min_matches[0]
    if len(min_matches) > 1:
        return min(
            min_matches,
            key=lambda t: float(t.max_amount) if t.max_amount is not None else float("inf"),
        )

    method_max = method.max_amount
    for t in sorted_tiers:
        if t.min_amount == method_min and t.max_amount == method_max:
            return t

    for t in sorted_tiers:
        if (t.label or "").strip() == DEFAULT_TIER_LABEL:
            return t

    for t in sorted_tiers:
        if re.search(r"\bunder\b", t.label or "", re.I):
            return t

    return sorted_tiers[0]


def _rotation_tier(tiers: list[PaymentMethodTier]) -> Optional[PaymentMethodTier]:
    sorted_tiers = _sort_tiers(tiers)
    for t in sorted_tiers:
        if re.search(r"\bover\b", t.label or "", re.I):
            return t
    if len(sorted_tiers) >= 2:
        return max(sorted_tiers, key=lambda t: float(t.min_amount or 0))
    return None


def _is_rotation_variant_label(label: str) -> bool:
    key = (label or "").strip().lower()
    return key in ROTATION_VARIANT_LABELS or bool(ROTATION_LABEL_RE.match((label or "").strip()))


def _response_fields_from_method(method: PaymentMethod) -> dict:
    return {
        "response_type": method.response_type or "text",
        "response_text": method.response_text,
        "response_file_id": method.response_file_id,
        "response_caption": method.response_caption,
        "use_group_checkout_link": bool(getattr(method, "use_group_checkout_link", False)),
        "group_checkout_provider": getattr(method, "group_checkout_provider", None) or "stripe",
        "hyperlink_text": getattr(method, "hyperlink_text", None) or "PAY HERE",
    }


def _copy_response_to_tier(
    tier: PaymentMethodTier, method: PaymentMethod, *, dry_run: bool
) -> bool:
    changed = False
    if not _tier_has_response(tier) and _method_has_response(method):
        fields = _response_fields_from_method(method)
        if tier.use_group_checkout_link:
            fields["use_group_checkout_link"] = True
            fields["group_checkout_provider"] = (
                tier.group_checkout_provider or fields["group_checkout_provider"]
            )
            fields["hyperlink_text"] = tier.hyperlink_text or fields["hyperlink_text"]
        for k, v in fields.items():
            setattr(tier, k, v)
        changed = True
    elif (
        bool(getattr(method, "use_group_checkout_link", False))
        and not bool(getattr(tier, "use_group_checkout_link", False))
        and _tier_has_response(tier)
    ):
        tier.use_group_checkout_link = True
        tier.group_checkout_provider = getattr(method, "group_checkout_provider", None) or "stripe"
        tier.hyperlink_text = getattr(method, "hyperlink_text", None) or "PAY HERE"
        changed = True
    if changed and dry_run:
        print(f"    [dry-run] would copy method response -> tier {tier.id} ({tier.label!r})")
    return changed


def _variant_copy_fields(src: MethodVariant) -> dict:
    return {
        "method_id": src.method_id,
        "label": src.label,
        "weight": src.weight,
        "response_type": src.response_type or "text",
        "response_text": src.response_text,
        "response_file_id": src.response_file_id,
        "response_caption": src.response_caption,
        "min_amount": src.min_amount,
        "max_amount": src.max_amount,
        "use_group_checkout_link": src.use_group_checkout_link,
        "group_checkout_provider": src.group_checkout_provider,
        "hyperlink_text": src.hyperlink_text,
        "sort_order": src.sort_order or 0,
    }


def _labels_on_tier(session, tier_id: int) -> set[str]:
    rows = (
        session.query(MethodVariant.label)
        .filter_by(tier_id=tier_id)
        .all()
    )
    return {(r[0] or "").strip().lower() for r in rows}


def _copy_method_variants(
    session,
    method: PaymentMethod,
    primary: PaymentMethodTier,
    rotation: Optional[PaymentMethodTier],
    *,
    dry_run: bool,
) -> int:
    copied = 0
    method_variants = (
        session.query(MethodVariant)
        .filter_by(method_id=method.id, tier_id=None)
        .order_by(MethodVariant.sort_order, MethodVariant.id)
        .all()
    )
    labels_by_tier: dict[int, set[str]] = {}

    for v in method_variants:
        target = rotation if (_is_rotation_variant_label(v.label) and rotation) else primary
        if target.id not in labels_by_tier:
            labels_by_tier[target.id] = _labels_on_tier(session, target.id)
        key = (v.label or "").strip().lower()
        if key in labels_by_tier[target.id]:
            continue
        if dry_run:
            print(
                f"    [dry-run] would copy method variant {v.id} {v.label!r} -> tier {target.id} ({target.label!r})"
            )
        else:
            session.add(MethodVariant(tier_id=target.id, **_variant_copy_fields(v)))
            session.flush()
        labels_by_tier[target.id].add(key)
        copied += 1
    return copied


def _rebalance_rotation_variants(
    session,
    primary: PaymentMethodTier,
    rotation: PaymentMethodTier,
    *,
    dry_run: bool,
) -> int:
    moved = 0
    under_variants = (
        session.query(MethodVariant)
        .filter_by(tier_id=primary.id)
        .order_by(MethodVariant.sort_order, MethodVariant.id)
        .all()
    )
    over_labels = _labels_on_tier(session, rotation.id)

    for v in under_variants:
        if not _is_rotation_variant_label(v.label):
            continue
        key = (v.label or "").strip().lower()
        if key in over_labels:
            if dry_run:
                print(
                    f"    [dry-run] would delete duplicate tier variant {v.id} {v.label!r} on {primary.label!r}"
                )
            else:
                session.delete(v)
            moved += 1
            continue
        if dry_run:
            print(
                f"    [dry-run] would move tier variant {v.id} {v.label!r}: {primary.label!r} -> {rotation.label!r}"
            )
        else:
            v.tier_id = rotation.id
        over_labels.add(key)
        moved += 1
    return moved


def _ensure_default_tier(
    session,
    method: PaymentMethod,
    *,
    dry_run: bool,
) -> Optional[PaymentMethodTier]:
    if dry_run:
        print(
            f"    [dry-run] would create default tier for method {method.id} ({method.name!r})"
        )
        return None
    tier = PaymentMethodTier(
        method_id=method.id,
        label=DEFAULT_TIER_LABEL,
        min_amount=method.min_amount,
        max_amount=method.max_amount,
        sort_order=0,
        **_response_fields_from_method(method),
    )
    session.add(tier)
    session.flush()
    return tier


def migrate_method(session, method: PaymentMethod, *, dry_run: bool) -> dict:
    stats = {"tiers_created": 0, "response_copied": 0, "variants_copied": 0, "variants_moved": 0}
    tiers = list(method.tiers or [])
    if not tiers:
        if not (
            _method_has_response(method)
            or bool(getattr(method, "use_group_checkout_link", False))
            or session.query(MethodVariant).filter_by(method_id=method.id, tier_id=None).count()
        ):
            return stats
        print(f"  method {method.id} {method.name!r}: no tiers — creating default")
        created = _ensure_default_tier(session, method, dry_run=dry_run)
        stats["tiers_created"] += 1
        if created:
            tiers = [created]
        elif dry_run:
            return stats

    primary = _primary_tier(tiers, method)
    if not primary:
        return stats

    if _copy_response_to_tier(primary, method, dry_run=dry_run):
        stats["response_copied"] += 1
        print(f"  method {method.id} {method.name!r}: copied response/stripe -> tier {primary.id} ({primary.label!r})")

    rotation = _rotation_tier(tiers)
    stats["variants_copied"] += _copy_method_variants(
        session, method, primary, rotation, dry_run=dry_run
    )
    if stats["variants_copied"]:
        print(
            f"  method {method.id} {method.name!r}: copied {stats['variants_copied']} method-level variant(s) to tiers (method rows kept)"
        )

    if rotation and rotation.id != primary.id:
        moved = _rebalance_rotation_variants(
            session, primary, rotation, dry_run=dry_run
        )
        stats["variants_moved"] = moved
        if moved:
            print(
                f"  method {method.id} {method.name!r}: rebalanced {moved} rotation variant(s) to tier {rotation.id} ({rotation.label!r})"
            )

    return stats


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args(list(argv) if argv is not None else None)

    totals = {"methods": 0, "tiers_created": 0, "response_copied": 0, "variants_copied": 0, "variants_moved": 0}

    with get_db() as session:
        methods = (
            session.query(PaymentMethod)
            .order_by(PaymentMethod.club_id, PaymentMethod.direction, PaymentMethod.sort_order, PaymentMethod.id)
            .all()
        )
        for method in methods:
            stats = migrate_method(session, method, dry_run=args.dry_run)
            if any(stats.values()):
                totals["methods"] += 1
                for k in ("tiers_created", "response_copied", "variants_copied", "variants_moved"):
                    totals[k] += stats[k]

        if args.dry_run:
            session.rollback()
            print("\nDry run — no changes written.")
        else:
            print("\nCommitted.")

    print(
        f"Done. Touched {totals['methods']} method(s): "
        f"{totals['tiers_created']} tier(s) created, "
        f"{totals['response_copied']} response/stripe copy, "
        f"{totals['variants_copied']} method variant copy, "
        f"{totals['variants_moved']} tier rebalance."
    )


if __name__ == "__main__":
    main()
