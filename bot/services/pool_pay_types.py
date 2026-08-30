"""Pool Pay category (union method vs large cashout) and structured slugs."""

from __future__ import annotations

import re
from typing import Literal, Optional, Tuple

from bot.services.union_method_types import UNION_TYPE_SLUGS, validate_union_method_type

PoolPayTypeSlug = Literal["union_method", "large_cashout"]

POOL_PAY_TYPES: dict[str, str] = {
    "union_method": "Union method",
    "large_cashout": "Large cashout",
}

POOL_PAY_TYPE_SLUGS = frozenset(POOL_PAY_TYPES.keys())

_SEGMENT_BY_POOL_PAY_TYPE: dict[str, str] = {
    "union_method": "union",
    "large_cashout": "lc",
}

_POOL_PAY_TYPE_BY_SEGMENT: dict[str, str] = {
    "union": "union_method",
    "lc": "large_cashout",
}

_SUFFIX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_pool_pay_type(value: str) -> str:
    key = (value or "").strip().lower()
    if key not in POOL_PAY_TYPES:
        raise ValueError(
            f"Pool pay type must be one of: {', '.join(sorted(POOL_PAY_TYPES))}."
        )
    return key


def pool_pay_type_display_name(slug: str) -> str:
    meta = POOL_PAY_TYPES.get(validate_pool_pay_type(slug))
    return meta or slug


def pool_pay_type_segment(pool_pay_type: str) -> str:
    key = validate_pool_pay_type(pool_pay_type)
    return _SEGMENT_BY_POOL_PAY_TYPE[key]


def normalize_identifier_suffix(raw: str) -> str:
    normalized = (raw or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise ValueError("Identifier suffix is required.")
    if not _SUFFIX_RE.match(normalized):
        raise ValueError(
            "Identifier suffix may only contain lowercase letters, numbers, and hyphens."
        )
    return normalized


def build_pool_pay_slug(type_slug: str, pool_pay_type: str, suffix: str) -> str:
    method_type = validate_union_method_type(type_slug)
    pay_type = validate_pool_pay_type(pool_pay_type)
    suffix_norm = normalize_identifier_suffix(suffix)
    segment = pool_pay_type_segment(pay_type)
    return f"{method_type}-{segment}-{suffix_norm}"


def parse_pool_pay_slug(slug: str) -> Optional[Tuple[str, str, str]]:
    """Return (type_slug, pool_pay_type, suffix) or None if not structured."""
    raw = (slug or "").strip().lower()
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) < 3:
        return None
    type_slug = parts[0]
    segment = parts[1]
    if type_slug not in UNION_TYPE_SLUGS:
        return None
    pool_pay_type = _POOL_PAY_TYPE_BY_SEGMENT.get(segment)
    if pool_pay_type is None:
        return None
    suffix = "-".join(parts[2:])
    if not suffix or not _SUFFIX_RE.match(suffix):
        return None
    return type_slug, pool_pay_type, suffix


def pool_pay_type_from_method(method) -> str:
    raw = getattr(method, "pool_pay_type", None)
    if raw:
        try:
            return validate_pool_pay_type(str(raw))
        except ValueError:
            pass
    parsed = parse_pool_pay_slug(getattr(method, "slug", "") or "")
    if parsed:
        return parsed[1]
    return "union_method"
