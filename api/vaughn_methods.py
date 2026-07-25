"""Identify Vaughn-owned ClubGTO deposit methods for reconcile export."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from api.audit_ledger import LedgerLine

# Payment tags stored on ledger Variant (zelle_recipient / venmo_handle).
VAUGHN_ZELLE_RECIPIENTS = frozenset({"2133729202"})
VAUGHN_VENMO_HANDLES = frozenset({"janseashells"})

_VAUGHN_CLUB_SLUG = "clubgto"


def normalize_zelle_recipient(tag: str) -> str:
    return "".join(ch for ch in (tag or "") if ch.isdigit())


def normalize_venmo_handle(tag: str) -> str:
    return (tag or "").strip().lstrip("@").lower()


def is_vaughn_method(
    *,
    source: str,
    variant: str | None,
    club_slug: str,
) -> bool:
    """True when this ledger deposit belongs to Vaughn's ClubGTO accounts."""
    if (source or "").strip() == "deposit_crypto":
        return club_slug.strip().lower() == _VAUGHN_CLUB_SLUG
    tag = (variant or "").strip()
    if source == "deposit_zelle":
        return normalize_zelle_recipient(tag) in VAUGHN_ZELLE_RECIPIENTS
    if source == "deposit_venmo":
        return normalize_venmo_handle(tag) in VAUGHN_VENMO_HANDLES
    return False


@dataclass(frozen=True)
class VaughnMethodTally:
    method_label: str
    tag: str
    count: int
    total_usd: Decimal


def _bucket_key(line: LedgerLine) -> tuple[str, str, str] | None:
    """Return (sort_key, method_label, display_tag) or None if not Vaughn."""
    source = line.source
    tag = (line.variant or "").strip()
    if source == "deposit_zelle":
        return ("1_zelle", "Zelle", normalize_zelle_recipient(tag) or tag)
    if source == "deposit_venmo":
        handle = normalize_venmo_handle(tag)
        return ("2_venmo", "Venmo", f"@{handle}" if handle else tag)
    if source == "deposit_crypto":
        return ("3_crypto", "Crypto", "(all ClubGTO)")
    return None


def tally_vaughn_methods(
    ledger_lines: list[LedgerLine],
    *,
    club_slug: str,
) -> list[VaughnMethodTally]:
    """Count and sum abs(USD) for Vaughn deposit ledger lines, by method."""
    buckets: dict[tuple[str, str, str], list[Decimal]] = {}
    for line in ledger_lines:
        if not is_vaughn_method(
            source=line.source,
            variant=line.variant,
            club_slug=club_slug,
        ):
            continue
        key = _bucket_key(line)
        if key is None:
            continue
        buckets.setdefault(key, []).append(abs(line.amount_signed))

    out: list[VaughnMethodTally] = []
    for sort_key, method_label, tag in sorted(buckets.keys()):
        amounts = buckets[(sort_key, method_label, tag)]
        out.append(
            VaughnMethodTally(
                method_label=method_label,
                tag=tag,
                count=len(amounts),
                total_usd=sum(amounts, Decimal(0)),
            )
        )
    return out
