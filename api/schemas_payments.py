from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PaymentProviderRead(BaseModel):
    id: str
    label: str


class StripeMethodOptionRead(BaseModel):
    id: int
    name: str
    slug: str


class PaginatedMeta(BaseModel):
    total: int
    limit: int
    offset: int


class StripeCustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_chat_id: int
    club_id: int
    gg_player_id: Optional[str] = None
    player_display_name: Optional[str] = None
    group_title: Optional[str] = None
    total_deposited_cents: int = 0
    total_deposited_usd: Decimal
    created_at: datetime


class StripeCustomerListResponse(BaseModel):
    items: list[StripeCustomerRead]
    total: int
    limit: int
    offset: int


class StripeCheckoutSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stripe_checkout_session_id: str
    stripe_customer_id: str
    telegram_chat_id: int
    club_id: int
    amount_cents: int
    amount_usd: Decimal
    currency: str
    status: str
    payment_method_id: Optional[int] = None
    method_name: Optional[str] = None
    method_slug: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    group_title: Optional[str] = None
    gg_player_id: Optional[str] = None
    player_display_name: Optional[str] = None
    stripe_dashboard_url: str
    stripe_payment_url: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StripeCheckoutSessionListResponse(BaseModel):
    items: list[StripeCheckoutSessionRead]
    total: int
    limit: int
    offset: int
