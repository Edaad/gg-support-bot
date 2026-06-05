from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Boolean,
    Numeric,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Club(Base):
    __tablename__ = "clubs"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    welcome_type = Column(String(10), default="text")
    welcome_text = Column(Text)
    welcome_file_id = Column(Text)
    welcome_caption = Column(Text)
    member_join_preamble_text = Column(Text)
    member_join_tos_file_id = Column(Text)
    member_join_tos_caption = Column(Text)
    list_type = Column(String(10), default="text")
    list_text = Column(Text)
    list_file_id = Column(Text)
    list_caption = Column(Text)
    allow_multi_cashout = Column(Boolean, default=True)
    allow_admin_commands = Column(Boolean, default=True)
    deposit_simple_mode = Column(Boolean, default=False)
    deposit_simple_type = Column(String(10), default="text")
    deposit_simple_text = Column(Text)
    deposit_simple_file_id = Column(Text)
    deposit_simple_caption = Column(Text)
    cashout_simple_mode = Column(Boolean, default=False)
    cashout_simple_type = Column(String(10), default="text")
    cashout_simple_text = Column(Text)
    cashout_simple_file_id = Column(Text)
    cashout_simple_caption = Column(Text)
    cashout_cooldown_enabled = Column(Boolean, default=False)
    cashout_cooldown_hours = Column(Integer, default=24)
    cashout_hours_enabled = Column(Boolean, default=False)
    cashout_hours_start = Column(String(5), default="08:00")
    cashout_hours_end = Column(String(5), default="23:00")
    cashout_max_amount = Column(Numeric(12, 2), nullable=True)
    cashout_soft_limit = Column(Numeric(12, 2), nullable=True)
    referral_enabled = Column(Boolean, default=False)
    first_deposit_bonus_enabled = Column(Boolean, default=False)
    first_deposit_bonus_pct = Column(Integer, default=0)
    first_deposit_bonus_cap = Column(Numeric(12, 2), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    payment_methods = relationship(
        "PaymentMethod", back_populates="club", cascade="all, delete-orphan"
    )
    custom_commands = relationship(
        "CustomCommand", back_populates="club", cascade="all, delete-orphan"
    )
    groups = relationship(
        "Group", back_populates="club", cascade="all, delete-orphan"
    )
    linked_accounts = relationship(
        "ClubLinkedAccount", back_populates="club", cascade="all, delete-orphan"
    )
    player_details = relationship(
        "PlayerDetails", back_populates="club", cascade="all, delete-orphan"
    )
    club_payment_methods = relationship(
        "ClubPaymentMethod", back_populates="club", cascade="all, delete-orphan"
    )


class ClubLinkedAccount(Base):
    """Additional Telegram user IDs for the same club (backup admins). Primary stays on Club.telegram_user_id."""

    __tablename__ = "club_linked_accounts"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club", back_populates="linked_accounts")


class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    __table_args__ = (
        UniqueConstraint("club_id", "direction", "slug", name="uq_club_direction_slug"),
        CheckConstraint("direction IN ('deposit', 'cashout')", name="ck_direction"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    direction = Column(String(10), nullable=False)
    name = Column(String(50), nullable=False)
    slug = Column(String(50), nullable=False)
    min_amount = Column(Numeric(12, 2), nullable=True)
    max_amount = Column(Numeric(12, 2), nullable=True)
    has_sub_options = Column(Boolean, default=False)
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    # Deposit methods can optionally embed a per-group generated link (currently Stripe Checkout) into response_text.
    use_group_checkout_link = Column(Boolean, default=False)
    group_checkout_provider = Column(String(32), nullable=True)
    hyperlink_text = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    deposit_limit = Column(Numeric(12, 2), nullable=True)
    accumulated_amount = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club", back_populates="payment_methods")
    sub_options = relationship(
        "PaymentSubOption", back_populates="method", cascade="all, delete-orphan"
    )
    tiers = relationship(
        "PaymentMethodTier", back_populates="method", cascade="all, delete-orphan",
        order_by="PaymentMethodTier.sort_order",
    )
    variants = relationship(
        "MethodVariant", back_populates="method", cascade="all, delete-orphan",
        order_by="MethodVariant.sort_order",
    )


class MethodVariant(Base):
    """Weighted response variants for a payment method or tier (load-balancing / rotation)."""

    __tablename__ = "method_variants"

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    tier_id = Column(
        Integer, ForeignKey("payment_method_tiers.id", ondelete="CASCADE"), nullable=True
    )
    label = Column(String(100), nullable=False)
    weight = Column(Integer, nullable=False, default=1)
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    min_amount = Column(Numeric(12, 2), nullable=True)
    max_amount = Column(Numeric(12, 2), nullable=True)
    use_group_checkout_link = Column(Boolean, nullable=True)
    group_checkout_provider = Column(String(32), nullable=True)
    hyperlink_text = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)

    method = relationship("PaymentMethod", back_populates="variants")
    tier = relationship("PaymentMethodTier", back_populates="variants")


class PaymentSubOption(Base):
    __tablename__ = "payment_sub_options"
    __table_args__ = (
        UniqueConstraint("method_id", "slug", name="uq_method_slug"),
    )

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(50), nullable=False)
    slug = Column(String(50), nullable=False)
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    method = relationship("PaymentMethod", back_populates="sub_options")


class PaymentMethodTier(Base):
    __tablename__ = "payment_method_tiers"

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    label = Column(String(50), nullable=False)
    min_amount = Column(Numeric(12, 2), nullable=True)
    max_amount = Column(Numeric(12, 2), nullable=True)
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    use_group_checkout_link = Column(Boolean, default=False)
    group_checkout_provider = Column(String(32), nullable=True)
    hyperlink_text = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)

    method = relationship("PaymentMethod", back_populates="tiers")
    variants = relationship(
        "MethodVariant", back_populates="tier", cascade="all, delete-orphan",
        order_by="MethodVariant.sort_order",
    )


# ── V2 payment config (greenfield; parallel to legacy payment_methods) ─────────


class ClubPaymentMethod(Base):
    """Method envelope only — player copy lives on tiers and tier variants."""

    __tablename__ = "club_payment_methods"
    __table_args__ = (
        UniqueConstraint("club_id", "direction", "slug", name="uq_cpm_club_direction_slug"),
        CheckConstraint("direction IN ('deposit', 'cashout')", name="ck_cpm_direction"),
        CheckConstraint(
            "min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount",
            name="ck_cpm_amount_range",
        ),
        Index("ix_cpm_club_direction_active", "club_id", "direction", "is_active", "sort_order"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False)
    direction = Column(String(10), nullable=False)
    name = Column(String(50), nullable=False)
    slug = Column(String(50), nullable=False)
    min_amount = Column(Numeric(12, 2), nullable=True)
    max_amount = Column(Numeric(12, 2), nullable=True)
    has_sub_options = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    deposit_limit = Column(Numeric(12, 2), nullable=True)
    accumulated_amount = Column(Numeric(12, 2), nullable=False, default=0)
    first_time_linking_enabled = Column(Boolean, nullable=False, default=False)
    first_time_bind_mode = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club", back_populates="club_payment_methods")
    tiers = relationship(
        "ClubPaymentTier",
        back_populates="method",
        cascade="all, delete-orphan",
        order_by="ClubPaymentTier.sort_order",
    )
    sub_options = relationship(
        "ClubPaymentSubOption",
        back_populates="method",
        cascade="all, delete-orphan",
        order_by="ClubPaymentSubOption.sort_order",
    )
    variants = relationship(
        "ClubPaymentTierVariant",
        back_populates="method",
        cascade="all, delete-orphan",
        order_by="ClubPaymentTierVariant.sort_order",
    )


class ClubPaymentTier(Base):
    __tablename__ = "club_payment_tiers"
    __table_args__ = (
        UniqueConstraint("method_id", "label", name="uq_cpt_method_label"),
        CheckConstraint(
            "min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount",
            name="ck_cpt_amount_range",
        ),
        Index("ix_cpt_method_sort", "method_id", "sort_order", "id"),
    )

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("club_payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    label = Column(String(50), nullable=False)
    min_amount = Column(Numeric(12, 2), nullable=True)
    max_amount = Column(Numeric(12, 2), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    response_type = Column(String(10), nullable=False, default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    use_group_checkout_link = Column(Boolean, nullable=False, default=False)
    group_checkout_provider = Column(String(32), nullable=True)
    hyperlink_text = Column(String(64), nullable=True)
    checkout_min_amount = Column(Numeric(12, 2), nullable=True)
    checkout_max_amount = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    method = relationship("ClubPaymentMethod", back_populates="tiers")
    variants = relationship(
        "ClubPaymentTierVariant",
        back_populates="tier",
        cascade="all, delete-orphan",
        order_by="ClubPaymentTierVariant.sort_order",
    )


class ClubPaymentTierVariant(Base):
    __tablename__ = "club_payment_tier_variants"
    __table_args__ = (
        UniqueConstraint("tier_id", "label", name="uq_cptv_tier_label"),
        CheckConstraint("weight >= 1", name="ck_cptv_weight"),
        Index("ix_cptv_tier_sort", "tier_id", "sort_order", "id"),
    )

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("club_payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    tier_id = Column(
        Integer, ForeignKey("club_payment_tiers.id", ondelete="CASCADE"), nullable=False
    )
    label = Column(String(100), nullable=False)
    weight = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    response_type = Column(String(10), nullable=False, default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    use_group_checkout_link = Column(Boolean, nullable=True)
    group_checkout_provider = Column(String(32), nullable=True)
    hyperlink_text = Column(String(64), nullable=True)
    checkout_min_amount = Column(Numeric(12, 2), nullable=True)
    checkout_max_amount = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    method = relationship("ClubPaymentMethod", back_populates="variants")
    tier = relationship("ClubPaymentTier", back_populates="variants")


class ClubPaymentSubOption(Base):
    __tablename__ = "club_payment_sub_options"
    __table_args__ = (UniqueConstraint("method_id", "slug", name="uq_cpso_method_slug"),)

    id = Column(Integer, primary_key=True)
    method_id = Column(
        Integer, ForeignKey("club_payment_methods.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(50), nullable=False)
    slug = Column(String(50), nullable=False)
    response_type = Column(String(10), nullable=False, default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    method = relationship("ClubPaymentMethod", back_populates="sub_options")


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (Index("ix_groups_club_id_name", "club_id", "name"),)

    chat_id = Column(BigInteger, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=True)
    first_deposit_claimed = Column(Boolean, default=False)
    added_at = Column(DateTime, server_default=func.now())

    club = relationship("Club", back_populates="groups")


class PlayerDetails(Base):
    """GG player id scoped to a club; Telegram group chats stored as bigint array (no FK to groups — DB cannot FK array elements)."""

    __tablename__ = "player_details"
    __table_args__ = (
        UniqueConstraint(
            "gg_player_id",
            "club_id",
            name="uq_player_details_gg_player_club",
        ),
        Index("ix_player_details_club_id", "club_id"),
        Index("ix_player_details_gg_player_id", "gg_player_id"),
        Index(
            "ix_player_details_chat_ids",
            "chat_ids",
            postgresql_using="gin",
        ),
    )

    id = Column(Integer, primary_key=True)
    chat_ids = Column(
        ARRAY(BigInteger),
        nullable=False,
        server_default=text("'{}'::bigint[]"),
    )
    gg_player_id = Column(String(255), nullable=False)
    gg_nickname = Column(String(255), nullable=True)
    club_id = Column(
        Integer,
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=False,
    )

    club = relationship("Club", back_populates="player_details")


class BroadcastJob(Base):
    __tablename__ = "broadcast_jobs"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(20), nullable=False, default="running")
    total_groups = Column(Integer, nullable=False, default=0)
    sent = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    errors_json = Column(Text, default="[]")
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)

    club = relationship("Club")


class PlayerActivity(Base):
    """Tracks completed deposits and cashouts per player per club for cooldown logic."""

    __tablename__ = "player_activities"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    activity_type = Column(String(10), nullable=False)  # "deposit" or "cashout"
    cancelled = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class CooldownBypass(Base):
    """Admin-granted cooldown bypasses per support group chat."""

    __tablename__ = "cooldown_bypasses"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    chat_id = Column(BigInteger, nullable=False)
    telegram_user_id = Column(BigInteger, nullable=True)  # legacy; unused for eligibility
    bypass_type = Column(String(20), nullable=False)  # "one_time" or "permanent"
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class CustomCommand(Base):
    __tablename__ = "custom_commands"
    __table_args__ = (
        UniqueConstraint("club_id", "command_name", name="uq_club_command"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    command_name = Column(String(32), nullable=False)
    response_type = Column(String(10), default="text")
    response_text = Column(Text)
    response_file_id = Column(Text)
    response_caption = Column(Text)
    customer_visible = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    club = relationship("Club", back_populates="custom_commands")


class BroadcastGroup(Base):
    """Named collection of group chats for targeted broadcasts."""

    __tablename__ = "broadcast_groups"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club")
    members = relationship(
        "BroadcastGroupMember",
        back_populates="broadcast_group",
        cascade="all, delete-orphan",
    )


class BroadcastGroupMember(Base):
    """Links a group chat to a broadcast group."""

    __tablename__ = "broadcast_group_members"
    __table_args__ = (
        UniqueConstraint("broadcast_group_id", "chat_id", name="uq_bg_chat"),
    )

    id = Column(Integer, primary_key=True)
    broadcast_group_id = Column(
        Integer, ForeignKey("broadcast_groups.id", ondelete="CASCADE"), nullable=False
    )
    chat_id = Column(BigInteger, nullable=False)

    broadcast_group = relationship("BroadcastGroup", back_populates="members")


class BonusType(Base):
    """Admin-configurable bonus categories shown in the /bonus flow."""

    __tablename__ = "bonus_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class BonusRecord(Base):
    """Individual bonus entries recorded via /bonus by admins."""

    __tablename__ = "bonus_records"

    id = Column(Integer, primary_key=True)
    player_username = Column(String(255), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    bonus_type_id = Column(
        Integer, ForeignKey("bonus_types.id", ondelete="SET NULL"), nullable=True
    )
    custom_description = Column(Text, nullable=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    admin_telegram_user_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    bonus_type = relationship("BonusType")
    club = relationship("Club")


class MtProtoSessionCredential(Base):
    """Portable Telethon StringSession payloads for MTProto (/gc); shared by web + worker."""

    __tablename__ = "mtproto_session_credentials"

    club_key = Column(String(64), primary_key=True)
    telethon_auth_string = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class CashierCashoutJob(Base):
    """Staff cashout wizard jobs (GGCashier bot)."""

    __tablename__ = "cashier_cashout_jobs"
    __table_args__ = (
        Index("ix_cashier_cashout_jobs_status", "status"),
        Index("ix_cashier_cashout_jobs_initiated_by", "initiated_by"),
        Index("ix_cashier_cashout_jobs_chat_id", "chat_id"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    chat_id = Column(BigInteger, nullable=False)
    group_title = Column(String(255), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    # IDs from legacy payment_methods or v2 club_payment_methods (no FK — backend varies).
    payment_method_id = Column(Integer, nullable=True)
    payment_sub_option_id = Column(Integer, nullable=True)
    method_display_name = Column(String(100), nullable=True)
    payout_details = Column(Text, nullable=True)
    trade_record_checked = Column(Boolean, default=False)
    cooldown_checked = Column(Boolean, default=False)
    initiated_by = Column(BigInteger, nullable=False)
    trigger = Column(String(20), nullable=False)  # group_cash | dm_cashout
    status = Column(String(20), nullable=False, default="initiated")
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    club = relationship("Club")


class SupportGroupChat(Base):
    """Megagroups created via /gc MTProto automation (per-club sessions)."""

    __tablename__ = "support_group_chats"
    __table_args__ = (
        Index("ix_support_group_chats_club_key", "club_key"),
        Index("ix_support_group_chats_telegram_chat_id", "telegram_chat_id"),
        Index("ix_support_group_chats_created_by", "created_by_telegram_user_id"),
        Index("ix_support_group_chats_created_at", "created_at"),
        Index("ix_support_group_chats_player_telegram_user_id", "player_telegram_user_id"),
        Index(
            "uq_support_group_chats_club_player",
            "club_key",
            "player_telegram_user_id",
            unique=True,
            postgresql_where=text("player_telegram_user_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True)
    club_key = Column(String(64), nullable=False)
    club_display_name = Column(String(255), nullable=False)
    player_telegram_user_id = Column(BigInteger, nullable=True)
    player_username = Column(Text, nullable=True)
    player_display_name = Column(Text, nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    telegram_chat_title = Column(Text, nullable=False)
    invite_link = Column(Text, nullable=True)
    created_by_telegram_user_id = Column(BigInteger, nullable=True)
    mtproto_session_name = Column(Text, nullable=True)
    added_users = Column(JSONB, nullable=True)
    failed_users = Column(JSONB, nullable=True)
    group_photo_path = Column(Text, nullable=True)
    initial_group_message_sent = Column(Boolean, nullable=False, default=False)
    player_dm_status = Column(Text, nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class StripeCustomer(Base):
    """Maps a support group chat to a stable Stripe Customer for debit-card deposits."""

    __tablename__ = "stripe_customers"
    __table_args__ = (
        Index("ix_stripe_customers_club_id", "club_id"),
        Index("ix_stripe_customers_stripe_customer_id", "stripe_customer_id"),
    )

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    stripe_customer_id = Column(String(255), unique=True, nullable=False)
    gg_player_id = Column(String(255), nullable=True)
    player_display_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")
    checkout_sessions = relationship(
        "StripeCheckoutSession",
        back_populates="customer",
        foreign_keys="StripeCheckoutSession.stripe_customer_id",
        primaryjoin="StripeCustomer.stripe_customer_id == StripeCheckoutSession.stripe_customer_id",
    )


class StripeCheckoutSession(Base):
    """One Stripe Checkout Session per /deposit Stripe request."""

    __tablename__ = "stripe_checkout_sessions"
    __table_args__ = (
        Index("ix_stripe_checkout_sessions_telegram_chat_id", "telegram_chat_id"),
        Index("ix_stripe_checkout_sessions_stripe_customer_id", "stripe_customer_id"),
        Index(
            "ix_stripe_checkout_sessions_stripe_checkout_session_id",
            "stripe_checkout_session_id",
        ),
        Index("ix_stripe_checkout_sessions_club_created", "club_id", "created_at"),
        Index("ix_stripe_checkout_sessions_club_status", "club_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    stripe_checkout_session_id = Column(String(255), unique=True, nullable=False)
    stripe_customer_id = Column(
        String(255),
        ForeignKey("stripe_customers.stripe_customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="usd")
    status = Column(String(20), nullable=False, default="open")
    payment_method_id = Column(Integer, nullable=True)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship(
        "StripeCustomer",
        back_populates="checkout_sessions",
        foreign_keys=[stripe_customer_id],
    )
    club = relationship("Club")


class VenmoPayment(Base):
    """One Venmo deposit ingested from Zapier; optionally bound to a support group."""

    __tablename__ = "venmo_payments"
    __table_args__ = (
        Index(
            "ix_venmo_payments_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_venmo_payments_telegram_chat_id", "telegram_chat_id"),
        Index("ix_venmo_payments_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    payer_name = Column(String(255), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    venmo_handle = Column(String(100), nullable=False)
    goods_or_services = Column(Boolean, nullable=False, default=False)
    paid_at = Column(String(255), nullable=True)
    source_external_id = Column(String(255), unique=True, nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    notification_chat_id = Column(BigInteger, nullable=True)
    notification_message_id = Column(BigInteger, nullable=True)
    bound_by_telegram_user_id = Column(BigInteger, nullable=True)
    auto_bound = Column(Boolean, nullable=False, default=False)
    is_test = Column(Boolean, nullable=False, default=False)
    bound_at = Column(DateTime(timezone=True), nullable=True)
    memo = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")


class VenmoPayerBinding(Base):
    """Remember last bind: normalized payer name -> support group (any shared Venmo)."""

    __tablename__ = "venmo_payer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "payer_name_normalized",
            name="uq_venmo_payer_bindings_payer_name",
        ),
        Index("ix_venmo_payer_bindings_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    payer_name_normalized = Column(String(255), nullable=False)
    venmo_handle = Column(String(100), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    last_bound_at = Column(DateTime(timezone=True), server_default=func.now())
    last_bound_by_telegram_user_id = Column(BigInteger, nullable=True)

    club = relationship("Club")


class PaymentMethodBindAttempt(Base):
    """In-flight first-time payment method setup (special amount or memo emoji)."""

    __tablename__ = "payment_method_bind_attempts"
    __table_args__ = (
        Index("ix_pmba_variant_status", "variant_id", "status"),
        Index(
            "ix_pmba_chat_method_status",
            "telegram_chat_id",
            "payment_method_slug",
            "status",
        ),
        Index("ix_pmba_created_at", "created_at"),
        Index(
            "uq_pmba_pending_variant_amount",
            "variant_id",
            "amount_cents",
            unique=True,
            postgresql_where=text(
                "status = 'pending' AND bind_kind = 'special_amount' "
                "AND amount_cents IS NOT NULL"
            ),
        ),
        Index(
            "uq_pmba_pending_variant_emoji",
            "variant_id",
            "setup_emoji",
            unique=True,
            postgresql_where=text(
                "status = 'pending' AND bind_kind = 'memo_emoji' "
                "AND setup_emoji IS NOT NULL"
            ),
        ),
    )

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    payment_method_slug = Column(String(32), nullable=False)
    method_id = Column(Integer, nullable=False)
    tier_id = Column(
        Integer, ForeignKey("club_payment_tiers.id", ondelete="SET NULL"), nullable=True
    )
    variant_id = Column(
        Integer,
        ForeignKey("club_payment_tier_variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    bind_kind = Column(String(32), nullable=False, default="special_amount")
    amount_cents = Column(Integer, nullable=True)
    setup_emoji = Column(String(32), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    bound_via = Column(String(32), nullable=False, default="special_amount")
    initiated_by_telegram_user_id = Column(BigInteger, nullable=True)
    venmo_payment_id = Column(
        Integer,
        ForeignKey("venmo_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    club = relationship("Club")
    variant = relationship("ClubPaymentTierVariant")
    venmo_payment = relationship("VenmoPayment")


class GroupPaymentMethodBinding(Base):
    """Per-group-chat link to a deposit payment method (e.g. Venmo variant)."""

    __tablename__ = "group_payment_method_bindings"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "payment_method_slug",
            name="uq_gpm_bindings_chat_method",
        ),
        Index("ix_gpm_bindings_telegram_chat_id", "telegram_chat_id"),
        Index("ix_gpm_bindings_club_slug", "club_id", "payment_method_slug"),
    )

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    payment_method_slug = Column(String(32), nullable=False)
    variant_id = Column(
        Integer,
        ForeignKey("club_payment_tier_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    venmo_handle = Column(String(100), nullable=True)
    bound_via = Column(String(32), nullable=False)
    bound_at = Column(DateTime(timezone=True), server_default=func.now())
    bound_by_telegram_user_id = Column(BigInteger, nullable=True)
    first_bind_attempt_id = Column(
        Integer,
        ForeignKey(
            "payment_method_bind_attempts.id",
            ondelete="SET NULL",
            name="fk_gpm_bindings_first_bind_attempt",
        ),
        nullable=True,
    )

    club = relationship("Club")
    variant = relationship("ClubPaymentTierVariant")
    first_bind_attempt = relationship(
        "PaymentMethodBindAttempt",
        foreign_keys=[first_bind_attempt_id],
    )
