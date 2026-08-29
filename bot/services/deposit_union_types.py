"""Deposit union labels (TMT / Massiv) for union payment methods."""

from __future__ import annotations

from typing import Literal

DepositUnionSlug = Literal["tmt", "massiv"]

DEPOSIT_UNIONS: dict[str, dict[str, str]] = {
    "tmt": {"name": "TMT"},
    "massiv": {"name": "Massiv"},
}

DEPOSIT_UNION_SLUGS = frozenset(DEPOSIT_UNIONS.keys())


def deposit_union_display_name(slug: str) -> str:
    meta = DEPOSIT_UNIONS.get((slug or "").strip().lower())
    if not meta:
        raise ValueError(f"Unknown deposit union: {slug!r}")
    return meta["name"]


def validate_deposit_union(slug: str) -> str:
    key = (slug or "").strip().lower()
    if key not in DEPOSIT_UNIONS:
        raise ValueError(f"Union must be one of: {', '.join(sorted(DEPOSIT_UNIONS))}.")
    return key
