"""Identify Vaughn-owned ClubGTO deposit methods for reconcile export."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from api.audit_ledger import DEPOSIT_METHOD_ORDER, LEDGER_SOURCE_LABELS, LedgerLine

# Payment tags stored on ledger Variant (zelle_recipient / venmo_handle).
VAUGHN_ZELLE_RECIPIENTS = frozenset({"2133729202"})
VAUGHN_VENMO_HANDLES = frozenset({"janseashells"})

_VAUGHN_CLUB_SLUG = "clubgto"
_DEPOSIT_SOURCES = frozenset(DEPOSIT_METHOD_ORDER)


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
    src = (source or "").strip()
    if src in ("deposit_crypto", "deposit_stripe"):
        return club_slug.strip().lower() == _VAUGHN_CLUB_SLUG
    tag = (variant or "").strip()
    if src == "deposit_zelle":
        return normalize_zelle_recipient(tag) in VAUGHN_ZELLE_RECIPIENTS
    if src == "deposit_venmo":
        return normalize_venmo_handle(tag) in VAUGHN_VENMO_HANDLES
    return False


def matching_source_label(
    *,
    source: str,
    variant: str | None,
    club_slug: str,
    source_label: str | None = None,
) -> str:
    """Matching Source cell text. ClubGTO deposits use RT/GTO ownership prefix."""
    base = (source_label or LEDGER_SOURCE_LABELS.get(source, "") or source).strip()
    if not base:
        return ""
    if club_slug.strip().lower() != _VAUGHN_CLUB_SLUG:
        return base
    src = (source or "").strip()
    if src not in _DEPOSIT_SOURCES:
        return base
    prefix = "GTO" if is_vaughn_method(
        source=src, variant=variant, club_slug=club_slug
    ) else "RT"
    return f"{prefix} {base}"


def clubgto_matching_source_options() -> tuple[str, ...]:
    """Source dropdown values for ClubGTO Matching (GTO = Vaughn, RT = other)."""
    deposit_labels: list[str] = []
    for src in DEPOSIT_METHOD_ORDER:
        base = LEDGER_SOURCE_LABELS[src]
        if src in ("deposit_stripe", "deposit_crypto"):
            deposit_labels.append(f"GTO {base}")
        elif src in ("deposit_zelle", "deposit_venmo"):
            deposit_labels.append(f"GTO {base}")
            deposit_labels.append(f"RT {base}")
        else:
            deposit_labels.append(f"RT {base}")
    non_deposit = [
        label
        for src, label in LEDGER_SOURCE_LABELS.items()
        if src not in _DEPOSIT_SOURCES
    ]
    return tuple(deposit_labels + non_deposit)


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
    if source == "deposit_stripe":
        return ("4_stripe", "Stripe", "(all ClubGTO)")
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
