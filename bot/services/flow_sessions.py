"""DB-backed per-chat flow session lifecycle (deposit/cashout UUID)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from db.connection import get_db
from db.models import (
    BotFlowSession,
    DepositFunnelEvent,
    PaymentMethodBindAttempt,
    StripeCheckoutSession,
)

logger = logging.getLogger(__name__)

FlowType = Literal["deposit", "cashout"]
SessionStatus = Literal["active", "completed", "abandoned"]

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"

END_REASON_SUPERSEDED = "superseded"
END_REASON_CANCELLED = "cancelled"
END_REASON_TIMEOUT = "timeout"
END_REASON_CHIPS_CREDITED = "chips_credited"

_STEP_DEPOSIT_STARTED = "deposit_started"


def _new_session_uuid() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class FlowSessionInfo:
    session_uuid: str
    telegram_chat_id: int
    flow_type: str
    status: str
    club_id: int | None
    telegram_user_id: int | None
    started_at: datetime


@dataclass(frozen=True)
class ResolvedDepositSession:
    deposit_session_id: str
    club_id: int | None
    telegram_user_id: int | None
    is_first_deposit: bool
    requires_method_setup: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_info(row: BotFlowSession) -> FlowSessionInfo:
    return FlowSessionInfo(
        session_uuid=str(row.session_uuid),
        telegram_chat_id=int(row.telegram_chat_id),
        flow_type=str(row.flow_type),
        status=str(row.status),
        club_id=int(row.club_id) if row.club_id is not None else None,
        telegram_user_id=(
            int(row.telegram_user_id) if row.telegram_user_id is not None else None
        ),
        started_at=row.started_at,
    )


def get_active_session(telegram_chat_id: int) -> FlowSessionInfo | None:
    with get_db() as session:
        row = (
            session.query(BotFlowSession)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                status=STATUS_ACTIVE,
            )
            .one_or_none()
        )
        if row is None:
            return None
        return _row_to_info(row)


def abandon_flow_session(session_uuid: str, *, end_reason: str) -> None:
    now = _utc_now()
    with get_db() as session:
        row = (
            session.query(BotFlowSession)
            .filter_by(session_uuid=str(session_uuid))
            .one_or_none()
        )
        if row is None or row.status != STATUS_ACTIVE:
            return
        row.status = STATUS_ABANDONED
        row.ended_at = now
        row.end_reason = end_reason


def complete_flow_session(session_uuid: str, *, end_reason: str = END_REASON_CHIPS_CREDITED) -> None:
    now = _utc_now()
    with get_db() as session:
        row = (
            session.query(BotFlowSession)
            .filter_by(session_uuid=str(session_uuid))
            .one_or_none()
        )
        if row is None or row.status != STATUS_ACTIVE:
            return
        row.status = STATUS_COMPLETED
        row.ended_at = now
        row.end_reason = end_reason


def start_flow_session(
    *,
    telegram_chat_id: int,
    flow_type: FlowType,
    club_id: int | None = None,
    telegram_user_id: int | None = None,
) -> str:
    """Start a new flow; supersede any existing active session for this chat."""
    now = _utc_now()
    session_uuid = _new_session_uuid()
    with get_db() as session:
        existing = (
            session.query(BotFlowSession)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                status=STATUS_ACTIVE,
            )
            .one_or_none()
        )
        if existing is not None:
            existing.status = STATUS_ABANDONED
            existing.ended_at = now
            existing.end_reason = END_REASON_SUPERSEDED
        row = BotFlowSession(
            session_uuid=session_uuid,
            telegram_chat_id=int(telegram_chat_id),
            flow_type=flow_type,
            status=STATUS_ACTIVE,
            club_id=int(club_id) if club_id is not None else None,
            telegram_user_id=(
                int(telegram_user_id) if telegram_user_id is not None else None
            ),
            started_at=now,
        )
        session.add(row)
    logger.info(
        "flow_session started uuid=%s chat_id=%s flow_type=%s",
        session_uuid,
        telegram_chat_id,
        flow_type,
    )
    return session_uuid


def _funnel_context_for_session(
    deposit_session_id: str,
) -> ResolvedDepositSession | None:
    with get_db() as session:
        started = (
            session.query(DepositFunnelEvent)
            .filter_by(
                deposit_session_id=str(deposit_session_id),
                step=_STEP_DEPOSIT_STARTED,
            )
            .one_or_none()
        )
        if started is not None:
            return ResolvedDepositSession(
                deposit_session_id=str(deposit_session_id),
                club_id=int(started.club_id) if started.club_id is not None else None,
                telegram_user_id=(
                    int(started.telegram_user_id)
                    if started.telegram_user_id is not None
                    else None
                ),
                is_first_deposit=bool(started.is_first_deposit),
                requires_method_setup=bool(started.requires_method_setup),
            )
        flow = (
            session.query(BotFlowSession)
            .filter_by(session_uuid=str(deposit_session_id))
            .one_or_none()
        )
        if flow is None:
            return None
        return ResolvedDepositSession(
            deposit_session_id=str(deposit_session_id),
            club_id=int(flow.club_id) if flow.club_id is not None else None,
            telegram_user_id=(
                int(flow.telegram_user_id)
                if flow.telegram_user_id is not None
                else None
            ),
            is_first_deposit=False,
            requires_method_setup=False,
        )


def resolve_deposit_session_id(
    *,
    bind_attempt_id: int | None = None,
    stripe_checkout_session_id: str | None = None,
    telegram_chat_id: int | None = None,
) -> ResolvedDepositSession | None:
    """Resolve deposit session UUID for payment ingest / bind paths."""
    session_id: str | None = None

    if bind_attempt_id is not None:
        with get_db() as session:
            attempt = (
                session.query(PaymentMethodBindAttempt)
                .filter_by(id=int(bind_attempt_id))
                .one_or_none()
            )
            if attempt is not None and attempt.deposit_session_id:
                session_id = str(attempt.deposit_session_id)

    if session_id is None and stripe_checkout_session_id:
        with get_db() as session:
            row = (
                session.query(StripeCheckoutSession)
                .filter_by(stripe_checkout_session_id=str(stripe_checkout_session_id))
                .one_or_none()
            )
            if row is not None and row.deposit_session_id:
                session_id = str(row.deposit_session_id)

    if session_id is None and telegram_chat_id is not None:
        active = get_active_session(int(telegram_chat_id))
        if active is not None and active.flow_type == "deposit":
            session_id = active.session_uuid

    if session_id is None:
        if telegram_chat_id is not None:
            logger.warning(
                "resolve_deposit_session_id: no session for chat_id=%s "
                "bind_attempt_id=%s stripe_session_id=%s",
                telegram_chat_id,
                bind_attempt_id,
                stripe_checkout_session_id,
            )
        return None

    return _funnel_context_for_session(session_id)


def get_deposit_session_context(deposit_session_id: str) -> ResolvedDepositSession | None:
    """Load funnel context for a known deposit session UUID."""
    return _funnel_context_for_session(str(deposit_session_id))
