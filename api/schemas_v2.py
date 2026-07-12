"""Pydantic schemas for greenfield club_payment_* API (/api/v2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class ClubPaymentSubOptionCreate(BaseModel):
    name: str
    slug: str
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class ClubPaymentSubOptionUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ClubPaymentSubOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method_id: int
    name: str
    slug: str
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    is_active: bool
    sort_order: int


class ClubPaymentTierVariantCreate(BaseModel):
    label: str
    weight: int = 1
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    use_group_checkout_link: Optional[bool] = None
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: int = 0


class ClubPaymentTierVariantUpdate(BaseModel):
    label: Optional[str] = None
    weight: Optional[int] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    use_group_checkout_link: Optional[bool] = None
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: Optional[int] = None
    tier_id: Optional[int] = None


class ClubPaymentTierVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method_id: int
    tier_id: int
    label: str
    weight: int
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    use_group_checkout_link: Optional[bool] = None
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: int


class ClubPaymentTierCreate(BaseModel):
    label: str
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    use_group_checkout_link: bool = False
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: int = 0


class ClubPaymentTierUpdate(BaseModel):
    label: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    use_group_checkout_link: Optional[bool] = None
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: Optional[int] = None


class ClubPaymentTierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method_id: int
    label: str
    min_amount: Optional[Decimal]
    max_amount: Optional[Decimal]
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    use_group_checkout_link: bool = False
    group_checkout_provider: Optional[str] = None
    hyperlink_text: Optional[str] = None
    checkout_min_amount: Optional[Decimal] = None
    checkout_max_amount: Optional[Decimal] = None
    sort_order: int
    variants: List[ClubPaymentTierVariantRead] = []


FirstTimeBindMode = Literal["special_amount", "memo_emoji"]


class ClubPaymentMethodCreate(BaseModel):
    direction: str
    name: str
    slug: str
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    has_sub_options: bool = False
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    deposit_limit: Optional[Decimal] = None
    first_time_linking_enabled: bool = False
    first_time_bind_mode: Optional[FirstTimeBindMode] = None


class ClubPaymentMethodUpdate(BaseModel):
    direction: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    has_sub_options: Optional[bool] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    sort_order: Optional[int] = None
    deposit_limit: Optional[Decimal] = None
    first_time_linking_enabled: Optional[bool] = None
    first_time_bind_mode: Optional[FirstTimeBindMode] = None


class ClubPaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    direction: str
    name: str
    slug: str
    min_amount: Optional[Decimal]
    max_amount: Optional[Decimal]
    has_sub_options: bool
    is_active: bool
    is_public: bool = True
    sort_order: int
    deposit_limit: Optional[Decimal] = None
    accumulated_amount: Optional[Decimal] = None
    first_time_linking_enabled: bool = False
    first_time_bind_mode: Optional[FirstTimeBindMode] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    sub_options: List[ClubPaymentSubOptionRead] = []
    tiers: List[ClubPaymentTierRead] = []
