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
    gg_nickname: Optional[str] = None
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
    gg_nickname: Optional[str] = None
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


class VenmoPaymentRead(BaseModel):
    id: int
    payer_name: str
    venmo_handle: str
    amount_cents: int
    amount_usd: Decimal
    goods_or_services: bool
    paid_at: Optional[str] = None
    group_title: Optional[str] = None
    gg_player_id: Optional[str] = None
    gg_nickname: Optional[str] = None
    club_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None
    status: str
    auto_bound: bool
    is_test: bool
    created_at: datetime
    bound_at: Optional[datetime] = None


class VenmoPaymentListResponse(BaseModel):
    items: list[VenmoPaymentRead]
    total: int
    limit: int
    offset: int


class VenmoPayerRead(BaseModel):
    payer_name: str
    venmo_handle: str
    group_title: Optional[str] = None
    gg_player_id: Optional[str] = None
    gg_nickname: Optional[str] = None
    total_deposited_cents: int
    total_deposited_usd: Decimal
    payment_count: int
    last_payment_at: Optional[datetime] = None


class VenmoPayerListResponse(BaseModel):
    items: list[VenmoPayerRead]
    total: int
    limit: int
    offset: int


class VenmoBindRequest(BaseModel):
    group_title: str


class VenmoBindResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    group_title: Optional[str] = None
    telegram_chat_id: Optional[int] = None
    club_id: Optional[int] = None
    payment: Optional[VenmoPaymentRead] = None


class BindingViaCount(BaseModel):
    bound_via: str
    count: int


class BindingAttemptFunnel(BaseModel):
    initiated: int
    succeeded: int
    expired: int
    cancelled: int
    pending: int
    success_rate: Optional[float] = None


class BindingSummaryResponse(BaseModel):
    payment_method_slug: str
    club_id: Optional[int] = None
    bindings_by_via: list[BindingViaCount]
    attempt_funnel: BindingAttemptFunnel


class GroupBindingRead(BaseModel):
    id: int
    telegram_chat_id: int
    club_id: int
    club_name: Optional[str] = None
    payment_method_slug: str
    variant_id: Optional[int] = None
    variant_label: Optional[str] = None
    venmo_handle: Optional[str] = None
    bound_via: str
    bound_at: datetime
    group_title: Optional[str] = None
    gg_player_id: Optional[str] = None


class GroupBindingListResponse(BaseModel):
    items: list[GroupBindingRead]
    total: int
    limit: int
    offset: int


class UnbindResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class BindAttemptRead(BaseModel):
    id: int
    telegram_chat_id: int
    club_id: int
    payment_method_slug: str
    variant_id: int
    amount_cents: int
    amount_usd: Decimal
    status: str
    bound_via: str
    venmo_payment_id: Optional[int] = None
    group_title: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    completed_at: Optional[datetime] = None


class BindAttemptListResponse(BaseModel):
    items: list[BindAttemptRead]
    total: int
    limit: int
    offset: int
