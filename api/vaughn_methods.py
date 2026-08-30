"""Identify Vaughn-owned ClubGTO deposit methods for reconcile export."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from api.audit_ledger import (
    CASHOUT_SOURCE_LABELS,
    DEPOSIT_METHOD_ORDER,
    LEDGER_SOURCE_LABELS,
    LedgerLine,
    UNION_MATCHING_SOURCE_OPTIONS,
)
from bot.services.payment_method_binding import (
    ZELLE_BANK_LABEL_TO_RECIPIENT,
    _zelle_bank_label_key,
    canonicalize_zelle_recipient,
)

# Payment tags stored on ledger Variant (zelle_recipient / venmo_handle).
VAUGHN_ZELLE_RECIPIENTS = frozenset(
    {
        "2133729202",
        "3105670961",
        "starship5vllc@gmail.com",
        "coachingstarship@gmail.com",
        "janvenmo@gmail.com",
        "clubgto1234@gmail.com",
    }
)
VAUGHN_VENMO_HANDLES = frozenset({"janseashells"})

_VAUGHN_CLUB_SLUG = "clubgto"
_DEPOSIT_SOURCES = frozenset(DEPOSIT_METHOD_ORDER)

_OWNER_SOURCE_PREFIX: dict[str, str] = {
    "round-table": "RT",
    "vaughn": "GTO",
    "mateos": "Mateos",
}
_OWNER_PREFIXED_DEPOSIT_SOURCES = frozenset(
    {
        "deposit_zelle",
        "deposit_venmo",
        "deposit_cashapp",
        "deposit_paypal",
        "deposit_crypto",
    }
)


def _owner_source_prefix(method_owner: str | None) -> str | None:
    if not method_owner:
        return None
    return _OWNER_SOURCE_PREFIX.get(method_owner.strip().lower())

VAUGHN_CASHOUT_SOURCE_LABELS: tuple[str, ...] = (
    "Vaughn Cashout Venmo",
    "Vaughn Cashout Cash App",
    "Vaughn Cashout Zelle",
    "Vaughn Cashout Crypto",
)


def memo_indicates_vaughn(memo: str | None) -> bool:
    """True when a payment memo/caption explicitly marks Vaughn ownership."""
    return "vaughn" in (memo or "").strip().lower()


def normalize_zelle_recipient(tag: str) -> str:
    canonical = canonicalize_zelle_recipient(tag)
    # Zapier sometimes appends a club label, e.g. "janvenmo@gmail.com (clubgto)".
    if " (" in canonical:
        canonical = canonical.split(" (", 1)[0].strip()
    return canonical


def normalize_venmo_handle(tag: str) -> str:
    return (tag or "").strip().lstrip("@").lower()


def is_vaughn_zelle_tag(tag: str) -> bool:
    """True when a Zelle recipient or Zapier bank label belongs to Vaughn/GTO."""
    raw = (tag or "").strip()
    if not raw:
        return False
    canonical = normalize_zelle_recipient(raw)
    if canonical in VAUGHN_ZELLE_RECIPIENTS:
        return True
    if "clubgto" in canonical:
        return True
    key = _zelle_bank_label_key(raw)
    if key in ZELLE_BANK_LABEL_TO_RECIPIENT:
        mapped = canonicalize_zelle_recipient(raw)
        return mapped in VAUGHN_ZELLE_RECIPIENTS or "clubgto" in mapped
    return False


def is_vaughn_method(
    *,
    source: str,
    variant: str | None,
    club_slug: str,
    memo: str | None = None,
) -> bool:
    """True when this ledger deposit belongs to Vaughn's ClubGTO accounts."""
    src = (source or "").strip()
    slug = club_slug.strip().lower()
    if src in ("deposit_crypto", "deposit_stripe"):
        return slug == _VAUGHN_CLUB_SLUG
    if slug == _VAUGHN_CLUB_SLUG and memo_indicates_vaughn(memo):
        if src in ("deposit_zelle", "deposit_venmo"):
            return True
    tag = (variant or "").strip()
    if src == "deposit_zelle":
        return is_vaughn_zelle_tag(tag)
    if src == "deposit_venmo":
        return normalize_venmo_handle(tag) in VAUGHN_VENMO_HANDLES
    return False


def matching_source_label(
    *,
    source: str,
    variant: str | None = None,
    club_slug: str = "",
    source_label: str | None = None,
    memo: str | None = None,
    method_owner: str | None = None,
) -> str:
    """Matching Source cell text with RT/GTO/Mateos owner prefix when applicable."""
    del club_slug, variant, memo  # kept for call-site compatibility
    src = (source or "").strip()
    if source_label and source_label.strip():
        base = source_label.strip()
    elif src in LEDGER_SOURCE_LABELS:
        base = LEDGER_SOURCE_LABELS[src]
    elif src.startswith("deposit_"):
        base = src[len("deposit_") :].replace("-", " ").replace("_", " ").strip().title()
    else:
        base = src
    if not base:
        return ""
    if src == "deposit_stripe":
        return "Stripe"
    if src in _OWNER_PREFIXED_DEPOSIT_SOURCES:
        prefix = _owner_source_prefix(method_owner)
        if prefix:
            return f"{prefix} {base}"
    return base


def owner_matching_source_options() -> tuple[str, ...]:
    """Source dropdown values with RT/GTO/Mateos owner prefixes on deposits."""
    deposit_labels: list[str] = []
    for src in DEPOSIT_METHOD_ORDER:
        base = LEDGER_SOURCE_LABELS[src]
        if src == "deposit_stripe":
            deposit_labels.append(base)
            continue
        for owner in ("round-table", "vaughn", "mateos"):
            prefix = _owner_source_prefix(owner)
            if prefix:
                deposit_labels.append(f"{prefix} {base}")
    non_deposit = [
        label
        for src, label in LEDGER_SOURCE_LABELS.items()
        if src not in _DEPOSIT_SOURCES and src != "cashout"
    ]
    return tuple(
        deposit_labels
        + non_deposit
        + list(UNION_MATCHING_SOURCE_OPTIONS)
        + list(CASHOUT_SOURCE_LABELS)
        + list(VAUGHN_CASHOUT_SOURCE_LABELS)
        + ["Free Play", "Back to Club", "GTO INC"]
    )


def clubgto_matching_source_options() -> tuple[str, ...]:
    """Deprecated alias; use owner_matching_source_options()."""
    return owner_matching_source_options()


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
            memo=line.memo,
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
