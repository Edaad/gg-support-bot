"""Export one flat CSV table of all clubs → methods → tiers → variants.

  python export_payment_config_table.py
  python export_payment_config_table.py --output backups/payment_config_table.csv

Columns are wide so you can filter/sort in Excel or Google Sheets.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

from dotenv import load_dotenv

load_dotenv()

from db.connection import get_db
from db.models import Club, MethodVariant, PaymentMethod, PaymentMethodTier

COLUMNS = [
    "row_kind",
    "club_id",
    "club_name",
    "method_id",
    "method_name",
    "direction",
    "method_slug",
    "method_active",
    "method_min",
    "method_max",
    "method_deposit_limit",
    "method_stripe",
    "method_has_response",
    "tier_id",
    "tier_label",
    "tier_min",
    "tier_max",
    "tier_stripe",
    "tier_has_response",
    "variant_id",
    "variant_label",
    "variant_weight",
    "variant_stripe",
    "response_type",
    "response_preview",
    "stripe_provider",
    "hyperlink_text",
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return format(value, "f").rstrip("0").rstrip(".") or "0"
    return str(value)


def _preview(text: Optional[str], limit: int = 120) -> str:
    if not text:
        return ""
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def _has_response(
    response_type: Optional[str],
    response_text: Optional[str],
    response_file_id: Optional[str],
) -> bool:
    rt = (response_type or "text").lower()
    if rt in ("file", "photo", "video"):
        return bool((response_file_id or "").strip())
    return bool((response_text or "").strip())


def _method_row(club: Club, method: PaymentMethod) -> dict[str, Any]:
    return {
        "row_kind": "method",
        "club_id": club.id,
        "club_name": club.name,
        "method_id": method.id,
        "method_name": method.name,
        "direction": method.direction,
        "method_slug": method.slug,
        "method_active": method.is_active,
        "method_min": method.min_amount,
        "method_max": method.max_amount,
        "method_deposit_limit": method.deposit_limit,
        "method_stripe": bool(getattr(method, "use_group_checkout_link", False)),
        "method_has_response": _has_response(
            method.response_type, method.response_text, method.response_file_id
        ),
        "tier_id": "",
        "tier_label": "",
        "tier_min": "",
        "tier_max": "",
        "tier_stripe": "",
        "tier_has_response": "",
        "variant_id": "",
        "variant_label": "",
        "variant_weight": "",
        "variant_stripe": "",
        "response_type": method.response_type or "text",
        "response_preview": _preview(method.response_text or method.response_caption),
        "stripe_provider": getattr(method, "group_checkout_provider", None) or "",
        "hyperlink_text": getattr(method, "hyperlink_text", None) or "",
    }


def _tier_row(club: Club, method: PaymentMethod, tier: PaymentMethodTier) -> dict[str, Any]:
    base = _method_row(club, method)
    base.update(
        {
            "row_kind": "tier",
            "tier_id": tier.id,
            "tier_label": tier.label,
            "tier_min": tier.min_amount,
            "tier_max": tier.max_amount,
            "tier_stripe": bool(getattr(tier, "use_group_checkout_link", False)),
            "tier_has_response": _has_response(
                tier.response_type, tier.response_text, tier.response_file_id
            ),
            "response_type": tier.response_type or "text",
            "response_preview": _preview(tier.response_text or tier.response_caption),
            "stripe_provider": getattr(tier, "group_checkout_provider", None) or "",
            "hyperlink_text": getattr(tier, "hyperlink_text", None) or "",
        }
    )
    return base


def _variant_row(
    club: Club,
    method: PaymentMethod,
    tier: Optional[PaymentMethodTier],
    variant: MethodVariant,
) -> dict[str, Any]:
    if tier:
        base = _tier_row(club, method, tier)
    else:
        base = _method_row(club, method)
    link = variant.use_group_checkout_link
    base.update(
        {
            "row_kind": "variant",
            "variant_id": variant.id,
            "variant_label": variant.label,
            "variant_weight": variant.weight,
            "variant_stripe": "yes" if link is True else ("no" if link is False else ""),
            "response_type": variant.response_type or "text",
            "response_preview": _preview(variant.response_text or variant.response_caption),
            "stripe_provider": variant.group_checkout_provider or "",
            "hyperlink_text": variant.hyperlink_text or "",
        }
    )
    return base


def build_rows(session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clubs = session.query(Club).order_by(Club.name, Club.id).all()
    for club in clubs:
        methods = (
            session.query(PaymentMethod)
            .filter_by(club_id=club.id)
            .order_by(PaymentMethod.direction, PaymentMethod.sort_order, PaymentMethod.id)
            .all()
        )
        for method in methods:
            rows.append(_method_row(club, method))

            tiers = (
                session.query(PaymentMethodTier)
                .filter_by(method_id=method.id)
                .order_by(PaymentMethodTier.sort_order, PaymentMethodTier.id)
                .all()
            )
            tier_by_id = {t.id: t for t in tiers}
            for tier in tiers:
                rows.append(_tier_row(club, method, tier))

            variants = (
                session.query(MethodVariant)
                .filter_by(method_id=method.id)
                .order_by(MethodVariant.tier_id.nullsfirst(), MethodVariant.sort_order, MethodVariant.id)
                .all()
            )
            for variant in variants:
                tier = tier_by_id.get(variant.tier_id) if variant.tier_id else None
                rows.append(_variant_row(club, method, tier, variant))

    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(row.get(k)) for k in COLUMNS})


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backups") / f"payment_config_table_{stamp}.csv",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    with get_db() as session:
        rows = build_rows(session)

    out = args.output.resolve()
    write_csv(out, rows)

    kinds = {}
    for r in rows:
        kinds[r["row_kind"]] = kinds.get(r["row_kind"], 0) + 1

    print(f"Wrote {out}")
    print(f"  {len(rows)} rows total: {kinds.get('method', 0)} methods, {kinds.get('tier', 0)} tiers, {kinds.get('variant', 0)} variants")


if __name__ == "__main__":
    main()
