"""Round Table Venmo and Cash App balance cards."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.account_balances import (
    AccountBalanceCard,
    list_account_balances,
    list_blocking_sends,
    update_account_balance_settings,
)
from api.auth import require_admin
from db.connection import get_db

router = APIRouter(prefix="/api/account-balances", tags=["account-balances"])


class AccountBalanceCardRead(BaseModel):
    method: str
    tag: str
    balance_cents: int
    offset_cents: int
    received_week_cents: int
    inflow_max_cents: int
    sending_remaining_cents: int
    sending_ceiling_cents: int
    send_power_maxed: bool
    inflow_maxed: bool


class AccountBalanceListResponse(BaseModel):
    venmo: list[AccountBalanceCardRead]
    cashapp: list[AccountBalanceCardRead]


class BlockingSendRead(BaseModel):
    id: int
    amount_cents: int
    recipient: str
    occurred_at: datetime
    expires_at: datetime


class AccountBalanceSettingsIn(BaseModel):
    offset_cents: int
    sending_ceiling_cents: int = Field(gt=0)
    inflow_max_cents: int = Field(gt=0)


def _card_read(card: AccountBalanceCard) -> AccountBalanceCardRead:
    return AccountBalanceCardRead(
        method=card.method,
        tag=card.tag,
        balance_cents=card.balance_cents,
        offset_cents=card.offset_cents,
        received_week_cents=card.received_week_cents,
        inflow_max_cents=card.inflow_max_cents,
        sending_remaining_cents=card.sending_remaining_cents,
        sending_ceiling_cents=card.sending_ceiling_cents,
        send_power_maxed=card.send_power_maxed,
        inflow_maxed=card.inflow_maxed,
    )


@router.get("", response_model=AccountBalanceListResponse)
def get_account_balances(_role: str = Depends(require_admin)):
    with get_db() as db:
        grouped = list_account_balances(db)
    return AccountBalanceListResponse(
        venmo=[_card_read(card) for card in grouped["venmo"]],
        cashapp=[_card_read(card) for card in grouped["cashapp"]],
    )


@router.get("/{method}/{tag}/sends", response_model=list[BlockingSendRead])
def get_blocking_sends(
    method: str,
    tag: str,
    _role: str = Depends(require_admin),
):
    try:
        with get_db() as db:
            rows = list_blocking_sends(db, method, tag)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return [
        BlockingSendRead(
            id=row.id,
            amount_cents=row.amount_cents,
            recipient=row.recipient,
            occurred_at=row.occurred_at,
            expires_at=row.expires_at,
        )
        for row in rows
    ]


@router.patch("/{method}/{tag}", response_model=AccountBalanceCardRead)
def patch_account_balance_settings(
    method: str,
    tag: str,
    body: AccountBalanceSettingsIn,
    _role: str = Depends(require_admin),
):
    try:
        with get_db() as db:
            card = update_account_balance_settings(
                db,
                method,
                tag,
                offset_cents=body.offset_cents,
                sending_ceiling_cents=body.sending_ceiling_cents,
                inflow_max_cents=body.inflow_max_cents,
            )
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _card_read(card)
