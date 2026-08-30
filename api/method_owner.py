"""Payment method owner slugs (account operator / ledger bucket)."""

from __future__ import annotations

from typing import Literal

from api.vaughn_methods import is_vaughn_method

METHOD_OWNERS: frozenset[str] = frozenset({"round-table", "vaughn", "mateos"})

MethodOwnerSlug = Literal["round-table", "vaughn", "mateos"]

METHOD_OWNER_ROUND_TABLE = "round-table"
METHOD_OWNER_VAUGHN = "vaughn"
METHOD_OWNER_MATEOS = "mateos"


def normalize_method_owner(value: str) -> str:
    """Return canonical method_owner slug or raise ValueError."""
    key = (value or "").strip().lower()
    if key not in METHOD_OWNERS:
        allowed = ", ".join(sorted(METHOD_OWNERS))
        raise ValueError(f"method_owner must be one of: {allowed}")
    return key


def infer_method_owner_for_backfill(
    *,
    source: str,
    variant: str | None,
    club_slug: str,
    memo: str | None = None,
) -> str:
    """Backfill method_owner from existing Vaughn reconcile heuristics."""
    if is_vaughn_method(
        source=source,
        variant=variant,
        club_slug=club_slug,
        memo=memo,
    ):
        return METHOD_OWNER_VAUGHN
    return METHOD_OWNER_ROUND_TABLE


def resolve_ingest_method_owner(
    *,
    source: str,
    variant: str | None,
    method_owner: str,
    memo: str | None = None,
    club_slug: str = "",
) -> str:
    """Apply variant-based Vaughn detection on top of Zapier method_owner."""
    owner = normalize_method_owner(method_owner)
    if owner == METHOD_OWNER_MATEOS:
        return owner
    if is_vaughn_method(
        source=source,
        variant=variant,
        club_slug=club_slug,
        memo=memo,
    ):
        return METHOD_OWNER_VAUGHN
    return owner
