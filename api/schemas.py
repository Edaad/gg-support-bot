from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, ConfigDict


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str


# ── Club ──────────────────────────────────────────────────────────────────────

class ClubCreate(BaseModel):
    name: str
    telegram_user_id: int
    welcome_type: str = "text"
    welcome_text: Optional[str] = None
    welcome_file_id: Optional[str] = None
    welcome_caption: Optional[str] = None
    list_type: str = "text"
    list_text: Optional[str] = None
    list_file_id: Optional[str] = None
    list_caption: Optional[str] = None
    allow_multi_cashout: bool = True
    allow_admin_commands: bool = True
    deposit_simple_mode: bool = False
    deposit_simple_type: str = "text"
    deposit_simple_text: Optional[str] = None
    deposit_simple_file_id: Optional[str] = None
    deposit_simple_caption: Optional[str] = None
    cashout_simple_mode: bool = False
    cashout_simple_type: str = "text"
    cashout_simple_text: Optional[str] = None
    cashout_simple_file_id: Optional[str] = None
    cashout_simple_caption: Optional[str] = None
    cashout_cooldown_enabled: bool = False
    cashout_cooldown_hours: int = 24
    cashout_hours_enabled: bool = False
    cashout_hours_start: str = "08:00"
    cashout_hours_end: str = "23:00"
    is_active: bool = True


class ClubUpdate(BaseModel):
    name: Optional[str] = None
    telegram_user_id: Optional[int] = None
    welcome_type: Optional[str] = None
    welcome_text: Optional[str] = None
    welcome_file_id: Optional[str] = None
    welcome_caption: Optional[str] = None
    list_type: Optional[str] = None
    list_text: Optional[str] = None
    list_file_id: Optional[str] = None
    list_caption: Optional[str] = None
    allow_multi_cashout: Optional[bool] = None
    allow_admin_commands: Optional[bool] = None
    deposit_simple_mode: Optional[bool] = None
    deposit_simple_type: Optional[str] = None
    deposit_simple_text: Optional[str] = None
    deposit_simple_file_id: Optional[str] = None
    deposit_simple_caption: Optional[str] = None
    cashout_simple_mode: Optional[bool] = None
    cashout_simple_type: Optional[str] = None
    cashout_simple_text: Optional[str] = None
    cashout_simple_file_id: Optional[str] = None
    cashout_simple_caption: Optional[str] = None
    cashout_cooldown_enabled: Optional[bool] = None
    cashout_cooldown_hours: Optional[int] = None
    cashout_hours_enabled: Optional[bool] = None
    cashout_hours_start: Optional[str] = None
    cashout_hours_end: Optional[str] = None
    is_active: Optional[bool] = None


class ClubRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    telegram_user_id: int
    welcome_type: Optional[str]
    welcome_text: Optional[str]
    welcome_file_id: Optional[str]
    welcome_caption: Optional[str]
    list_type: Optional[str]
    list_text: Optional[str]
    list_file_id: Optional[str]
    list_caption: Optional[str]
    allow_multi_cashout: bool
    allow_admin_commands: bool
    deposit_simple_mode: bool
    deposit_simple_type: Optional[str]
    deposit_simple_text: Optional[str]
    deposit_simple_file_id: Optional[str]
    deposit_simple_caption: Optional[str]
    cashout_simple_mode: bool
    cashout_simple_type: Optional[str]
    cashout_simple_text: Optional[str]
    cashout_simple_file_id: Optional[str]
    cashout_simple_caption: Optional[str]
    cashout_cooldown_enabled: bool
    cashout_cooldown_hours: int
    cashout_hours_enabled: bool
    cashout_hours_start: Optional[str]
    cashout_hours_end: Optional[str]
    is_active: bool
    created_at: Optional[datetime]
    method_count: int = 0
    group_count: int = 0
    linked_account_count: int = 0


class LinkedAccountCreate(BaseModel):
    telegram_user_id: int


class LinkedAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    telegram_user_id: int
    created_at: Optional[datetime]


# ── Payment Method ────────────────────────────────────────────────────────────

class MethodCreate(BaseModel):
    direction: str
    name: str
    slug: str
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    has_sub_options: bool = False
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class MethodUpdate(BaseModel):
    direction: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    has_sub_options: Optional[bool] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class SubOptionRead(BaseModel):
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


class VariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method_id: int
    label: str
    weight: int
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    sort_order: int


class MethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    direction: str
    name: str
    slug: str
    min_amount: Optional[Decimal]
    max_amount: Optional[Decimal]
    has_sub_options: bool
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    is_active: bool
    sort_order: int
    created_at: Optional[datetime]
    sub_options: List[SubOptionRead] = []
    tiers: List[TierRead] = []
    variants: List[VariantRead] = []


# ── Payment Method Tier ───────────────────────────────────────────────────────

class TierCreate(BaseModel):
    label: str
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    sort_order: int = 0


class TierUpdate(BaseModel):
    label: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    sort_order: Optional[int] = None


class TierRead(BaseModel):
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
    sort_order: int


# ── Method Variant (weighted rotation) ────────────────────────────────────────

class VariantCreate(BaseModel):
    label: str
    weight: int = 1
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    sort_order: int = 0


class VariantUpdate(BaseModel):
    label: Optional[str] = None
    weight: Optional[int] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    sort_order: Optional[int] = None


# ── Payment Sub-Option ────────────────────────────────────────────────────────

class SubOptionCreate(BaseModel):
    name: str
    slug: str
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class SubOptionUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


# ── Custom Command ────────────────────────────────────────────────────────────

class CommandCreate(BaseModel):
    command_name: str
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    customer_visible: bool = False
    is_active: bool = True


class CommandUpdate(BaseModel):
    command_name: Optional[str] = None
    response_type: Optional[str] = None
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None
    customer_visible: Optional[bool] = None
    is_active: Optional[bool] = None


class CommandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    command_name: str
    response_type: Optional[str]
    response_text: Optional[str]
    response_file_id: Optional[str]
    response_caption: Optional[str]
    customer_visible: bool
    is_active: bool


# ── Group ─────────────────────────────────────────────────────────────────────

class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: int
    club_id: int
    added_at: Optional[datetime]


# ── Simulate ──────────────────────────────────────────────────────────────────

class SimulateMethodOut(BaseModel):
    id: int
    name: str
    slug: str
    min_amount: Optional[Decimal]
    max_amount: Optional[Decimal]
    has_sub_options: bool
    response_type: Optional[str]
    response_text: Optional[str]
    response_caption: Optional[str]
    sub_options: List[SubOptionRead] = []


class SimulateResponse(BaseModel):
    club_name: str
    direction: str
    methods: List[SimulateMethodOut]
