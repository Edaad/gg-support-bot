"""Fixed union method types (Zelle, Cash App, Apple Pay)."""

from __future__ import annotations

from typing import Literal, Optional

UnionMethodTypeSlug = Literal["zelle", "cashapp", "applepay"]

UNION_METHOD_TYPES: dict[str, dict[str, str]] = {
    "zelle": {"name": "Zelle", "club_slug": "zelle"},
    "cashapp": {"name": "Cash App", "club_slug": "cashapp"},
    "applepay": {"name": "Apple Pay", "club_slug": "applepay"},
}

UNION_TYPE_SLUGS = frozenset(UNION_METHOD_TYPES.keys())
CLUB_SLUG_TO_UNION_TYPE = {
    meta["club_slug"]: slug for slug, meta in UNION_METHOD_TYPES.items()
}


def union_type_display_name(type_slug: str) -> str:
    meta = UNION_METHOD_TYPES.get(type_slug)
    if not meta:
        raise ValueError(f"Unknown union method type: {type_slug!r}")
    return meta["name"]


def union_type_from_display_name(name: str) -> Optional[str]:
    normalized = (name or "").strip()
    for slug, meta in UNION_METHOD_TYPES.items():
        if meta["name"] == normalized:
            return slug
    return None


def validate_union_method_type(type_slug: str) -> str:
    key = (type_slug or "").strip().lower()
    if key not in UNION_METHOD_TYPES:
        raise ValueError(
            f"Method must be one of: {', '.join(sorted(UNION_METHOD_TYPES))}."
        )
    return key
