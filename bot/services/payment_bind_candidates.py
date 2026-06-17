"""Shared candidate-group lookup and upsert for payment identity bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TYPE_CHECKING

from bot.services.payment_method_binding import (
    normalize_paypal_email,
    normalize_zelle_recipient,
    _normalize_cashapp_handle,
    _normalize_payer_name,
    _normalize_venmo_handle,
)
from db.models import (
    CashAppPayerBinding,
    CryptoWalletBinding,
    PayPalPayerBinding,
    VenmoPayerBinding,
    ZellePayerBinding,
)

import logging

from api.payments_helpers import is_analytics_excluded_group_title
from bot.services.payment_bind_logging import log_binding_table_write, log_candidate_list

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.services.venmo_payments import BoundGroup

MethodSlug = Literal["venmo", "zelle", "cashapp", "paypal", "crypto"]

METHOD_SHORT: dict[str, str] = {
    "venmo": "v",
    "zelle": "z",
    "crypto": "c",
    "cashapp": "a",
    "paypal": "p",
}

METHOD_FROM_SHORT: dict[str, str] = {v: k for k, v in METHOD_SHORT.items()}


def _candidate_matches_test_scope(group_title: str, *, test_scope: bool) -> bool:
    """True when a candidate group title belongs in the given test/production scope."""
    is_test_group = is_analytics_excluded_group_title(group_title)
    return is_test_group if test_scope else not is_test_group


def _filter_candidates_by_test_scope(
    candidates: list[CandidateGroup],
    test_scope: bool | None,
) -> list[CandidateGroup]:
    if test_scope is None:
        return candidates
    return [
        c
        for c in candidates
        if _candidate_matches_test_scope(c.group_title, test_scope=test_scope)
    ]


def bind_scope_mismatch_error(*, payment_is_test: bool, group_title: str) -> str | None:
    """Reject bind targets that cross test vs production group scope."""
    is_test_group = is_analytics_excluded_group_title(group_title)
    if payment_is_test and not is_test_group:
        return (
            "Test payments can only bind to test/staging groups "
            "(title ending in / TEST or containing @jz034)."
        )
    if not payment_is_test and is_test_group:
        return "Production payments cannot bind to test/staging groups."
    return None


def identity_label(
    method_slug: str,
    *,
    payer_name: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
) -> str:
    slug = (method_slug or "").strip().lower()
    if slug == "crypto":
        return f"from_address={from_address!r} alert_scope={alert_scope!r}"
    return f"payer_name={payer_name!r}"


@dataclass(frozen=True)
class CandidateGroup:
    telegram_chat_id: int
    club_id: int
    group_title: str

    def as_bound_group(self) -> BoundGroup:
        from bot.services.venmo_payments import BoundGroup

        return BoundGroup(
            telegram_chat_id=int(self.telegram_chat_id),
            club_id=int(self.club_id),
            group_title=self.group_title,
        )


def _row_to_candidate(
    *,
    telegram_chat_id: int,
    club_id: int | None,
    bound_group_title_at_bind: str | None,
) -> CandidateGroup | None:
    from bot.services.venmo_payments import resolve_display_group_title

    live_title = resolve_display_group_title(int(telegram_chat_id))
    title = (live_title or bound_group_title_at_bind or "").strip()
    if not title or club_id is None:
        return None
    return CandidateGroup(
        telegram_chat_id=int(telegram_chat_id),
        club_id=int(club_id),
        group_title=title,
    )


def list_candidate_groups(
    session,
    method_slug: str,
    *,
    payer_name: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
    filter_alert_scope: str | None = None,
    test_scope: bool | None = None,
) -> list[CandidateGroup]:
    """Return all known group candidates for a payment identity."""
    slug = (method_slug or "").strip().lower()
    candidates: list[CandidateGroup] = []

    if slug in ("venmo", "zelle", "cashapp", "paypal"):
        normalized = _normalize_payer_name(payer_name or "")
        if not normalized:
            return []
        model_map = {
            "venmo": VenmoPayerBinding,
            "zelle": ZellePayerBinding,
            "cashapp": CashAppPayerBinding,
            "paypal": PayPalPayerBinding,
        }
        rows = (
            session.query(model_map[slug])
            .filter_by(payer_name_normalized=normalized)
            .order_by(model_map[slug].last_bound_at.desc())
            .all()
        )
        for row in rows:
            candidate = _row_to_candidate(
                telegram_chat_id=int(row.telegram_chat_id),
                club_id=row.club_id,
                bound_group_title_at_bind=row.bound_group_title_at_bind,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates = _filter_candidates_by_test_scope(candidates, test_scope)
        log_candidate_list(
            method_slug=slug,
            identity_label=identity_label(slug, payer_name=payer_name),
            candidates=candidates,
            filter_alert_scope=filter_alert_scope,
        )
        return candidates

    if slug == "crypto":
        from bot.services.crypto_payments import alert_scope_for_club_id, normalize_from_address

        normalized_addr = normalize_from_address(from_address or "")
        scope = (alert_scope or "").strip()
        if not normalized_addr or not scope:
            return []
        rows = (
            session.query(CryptoWalletBinding)
            .filter_by(from_address_normalized=normalized_addr, alert_scope=scope)
            .order_by(CryptoWalletBinding.last_bound_at.desc())
            .all()
        )
        scope_filter = (filter_alert_scope or scope).strip()
        for row in rows:
            if row.club_id is not None and scope_filter:
                row_scope = alert_scope_for_club_id(int(row.club_id))
                if row_scope is not None and row_scope != scope_filter:
                    continue
            candidate = _row_to_candidate(
                telegram_chat_id=int(row.telegram_chat_id),
                club_id=row.club_id,
                bound_group_title_at_bind=row.bound_group_title_at_bind,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates = _filter_candidates_by_test_scope(candidates, test_scope)
        log_candidate_list(
            method_slug=slug,
            identity_label=identity_label(
                slug,
                from_address=from_address,
                alert_scope=alert_scope,
            ),
            candidates=candidates,
            filter_alert_scope=filter_alert_scope,
        )
        return candidates

    return []


def candidate_chat_ids(
    session,
    method_slug: str,
    *,
    payer_name: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
) -> list[int]:
    return [
        int(c.telegram_chat_id)
        for c in list_candidate_groups(
            session,
            method_slug,
            payer_name=payer_name,
            from_address=from_address,
            alert_scope=alert_scope,
        )
    ]


def add_candidate_group(
    session,
    method_slug: str,
    *,
    payer_name: str | None = None,
    method_handle: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: int | None = None,
) -> None:
    """Upsert a candidate row without binding a payment."""
    upsert_candidate_on_bind(
        session,
        method_slug,
        payer_name=payer_name,
        method_handle=method_handle,
        from_address=from_address,
        alert_scope=alert_scope,
        telegram_chat_id=telegram_chat_id,
        club_id=club_id,
        bound_group_title_at_bind=bound_group_title_at_bind,
        bound_by_telegram_user_id=bound_by_telegram_user_id,
    )


def upsert_candidate_on_bind(
    session,
    method_slug: str,
    *,
    payer_name: str | None = None,
    method_handle: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: int | None = None,
) -> None:
    """Upsert candidate row keyed by (identity, telegram_chat_id)."""
    slug = (method_slug or "").strip().lower()
    now = datetime.now(timezone.utc)
    chat_id = int(telegram_chat_id)

    if slug == "venmo":
        normalized = _normalize_payer_name(payer_name or "")
        handle = _normalize_venmo_handle(method_handle or "")
        row = (
            session.query(VenmoPayerBinding)
            .filter_by(payer_name_normalized=normalized, telegram_chat_id=chat_id)
            .one_or_none()
        )
        if row is None:
            row = VenmoPayerBinding(
                payer_name_normalized=normalized,
                venmo_handle=handle,
                telegram_chat_id=chat_id,
            )
            session.add(row)
        row.venmo_handle = handle
    elif slug == "zelle":
        normalized = _normalize_payer_name(payer_name or "")
        recipient = normalize_zelle_recipient(method_handle or "")
        row = (
            session.query(ZellePayerBinding)
            .filter_by(payer_name_normalized=normalized, telegram_chat_id=chat_id)
            .one_or_none()
        )
        if row is None:
            row = ZellePayerBinding(
                payer_name_normalized=normalized,
                zelle_recipient=recipient,
                telegram_chat_id=chat_id,
            )
            session.add(row)
        row.zelle_recipient = recipient
    elif slug == "cashapp":
        normalized = _normalize_payer_name(payer_name or "")
        handle = _normalize_cashapp_handle(method_handle or "")
        row = (
            session.query(CashAppPayerBinding)
            .filter_by(payer_name_normalized=normalized, telegram_chat_id=chat_id)
            .one_or_none()
        )
        if row is None:
            row = CashAppPayerBinding(
                payer_name_normalized=normalized,
                cashapp_handle=handle,
                telegram_chat_id=chat_id,
            )
            session.add(row)
        row.cashapp_handle = handle
    elif slug == "paypal":
        normalized = _normalize_payer_name(payer_name or "")
        email = normalize_paypal_email(method_handle or "")
        row = (
            session.query(PayPalPayerBinding)
            .filter_by(payer_name_normalized=normalized, telegram_chat_id=chat_id)
            .one_or_none()
        )
        if row is None:
            row = PayPalPayerBinding(
                payer_name_normalized=normalized,
                paypal_email=email,
                telegram_chat_id=chat_id,
            )
            session.add(row)
        row.paypal_email = email
    elif slug == "crypto":
        from bot.services.crypto_payments import normalize_from_address

        normalized_addr = normalize_from_address(from_address or "")
        scope = (alert_scope or "").strip()
        row = (
            session.query(CryptoWalletBinding)
            .filter_by(
                from_address_normalized=normalized_addr,
                alert_scope=scope,
                telegram_chat_id=chat_id,
            )
            .one_or_none()
        )
        if row is None:
            row = CryptoWalletBinding(
                from_address_normalized=normalized_addr,
                alert_scope=scope,
                telegram_chat_id=chat_id,
            )
            session.add(row)
    else:
        raise ValueError(f"Unknown payment method slug: {method_slug!r}")

    row.telegram_chat_id = chat_id
    row.club_id = int(club_id)
    row.bound_group_title_at_bind = bound_group_title_at_bind[:255]
    row.last_bound_at = now
    row.last_bound_by_telegram_user_id = bound_by_telegram_user_id
    log_binding_table_write(
        operation="upsert",
        method_slug=slug,
        identity_label=identity_label(
            slug,
            payer_name=payer_name,
            from_address=from_address,
            alert_scope=alert_scope,
        ),
        telegram_chat_id=chat_id,
        club_id=int(club_id),
        bound_group_title=bound_group_title_at_bind,
        actor_telegram_user_id=bound_by_telegram_user_id,
    )


def reset_all_candidates(
    session,
    method_slug: str,
    *,
    payer_name: str | None = None,
    from_address: str | None = None,
    alert_scope: str | None = None,
) -> int:
    """Delete all candidate rows for an identity. Returns rows deleted."""
    slug = (method_slug or "").strip().lower()

    if slug in ("venmo", "zelle", "cashapp", "paypal"):
        normalized = _normalize_payer_name(payer_name or "")
        if not normalized:
            return 0
        model_map = {
            "venmo": VenmoPayerBinding,
            "zelle": ZellePayerBinding,
            "cashapp": CashAppPayerBinding,
            "paypal": PayPalPayerBinding,
        }
        deleted = (
            session.query(model_map[slug])
            .filter_by(payer_name_normalized=normalized)
            .delete(synchronize_session=False)
        )
        log_binding_table_write(
            operation="reset_all",
            method_slug=slug,
            identity_label=identity_label(slug, payer_name=payer_name),
            telegram_chat_id=0,
            rows_affected=int(deleted),
        )
        return int(deleted)

    if slug == "crypto":
        from bot.services.crypto_payments import alert_scope_for_club_id, normalize_from_address

        normalized_addr = normalize_from_address(from_address or "")
        scope = (alert_scope or "").strip()
        if not normalized_addr or not scope:
            return 0
        deleted = (
            session.query(CryptoWalletBinding)
            .filter_by(from_address_normalized=normalized_addr, alert_scope=scope)
            .delete(synchronize_session=False)
        )
        log_binding_table_write(
            operation="reset_all",
            method_slug=slug,
            identity_label=identity_label(
                slug,
                from_address=from_address,
                alert_scope=alert_scope,
            ),
            telegram_chat_id=0,
            rows_affected=int(deleted),
        )
        return int(deleted)

    return 0


def method_handle_for_payment(payment: object, method_slug: str) -> str | None:
    slug = (method_slug or "").strip().lower()
    if slug == "venmo":
        return getattr(payment, "venmo_handle", None)
    if slug == "zelle":
        return getattr(payment, "zelle_recipient", None)
    if slug == "cashapp":
        return getattr(payment, "cashapp_handle", None)
    if slug == "paypal":
        return getattr(payment, "paypal_email", None)
    return None


def identity_kwargs_for_payment(payment: object, method_slug: str) -> dict:
    slug = (method_slug or "").strip().lower()
    if slug == "crypto":
        return {
            "from_address": getattr(payment, "from_address", None),
            "alert_scope": getattr(payment, "alert_scope", None),
        }
    return {"payer_name": getattr(payment, "payer_name", None)}


def candidates_for_payment(
    session,
    payment: object,
    method_slug: str,
    *,
    filter_alert_scope: str | None = None,
    test_scope: bool | None = None,
) -> list[CandidateGroup]:
    kwargs = identity_kwargs_for_payment(payment, method_slug)
    if filter_alert_scope is not None:
        kwargs["filter_alert_scope"] = filter_alert_scope
    if test_scope is None:
        test_scope = bool(getattr(payment, "is_test", False))
    kwargs["test_scope"] = test_scope
    return list_candidate_groups(session, method_slug, **kwargs)
