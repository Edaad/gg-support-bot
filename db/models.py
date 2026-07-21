from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Boolean,
    Numeric,
    Date,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
    Index,
    LargeBinary,
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
    auto_chip_adding_enabled = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    auto_deposit_on_payment_enabled = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    auto_claim_enabled = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    enable_popup_keyboard = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
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
    is_public = Column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
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
    deposit_method_access = relationship(
        "GroupDepositMethodAccess",
        back_populates="method",
        cascade="all, delete-orphan",
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
    # Last customer-chosen Round Table deposit union ("RT" or "AT"), used to route
    # auto chip-adding to the correct ClubGG club (Round Table vs Aces Table).
    last_deposit_union = Column(String(2), nullable=True)
    last_deposit_union_at = Column(DateTime(timezone=True), nullable=True)
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
    activity_type = Column(String(10), nullable=False)  # deposit, cashout, earlyrb, dep_cmd, add_cmd
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
    __table_args__ = (
        Index("ix_bonus_records_gg_player_id", "gg_player_id"),
        Index("ix_bonus_records_player_details_id", "player_details_id"),
    )

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
    player_details_id = Column(
        Integer,
        ForeignKey("player_details.id", ondelete="SET NULL"),
        nullable=True,
    )
    gg_player_id = Column(String(255), nullable=True)
    chat_id = Column(BigInteger, nullable=True)
    group_title = Column(String(512), nullable=True)
    admin_telegram_user_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    bonus_type = relationship("BonusType")
    club = relationship("Club")
    player_details = relationship("PlayerDetails")


class BonusDraft(Base):
    """Pending bonus recording started from a support group /add with a bonus amount."""

    __tablename__ = "bonus_drafts"
    __table_args__ = (
        Index("ix_bonus_drafts_staff_user_id", "staff_telegram_user_id"),
        Index("ix_bonus_drafts_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    staff_telegram_user_id = Column(BigInteger, nullable=False)
    club_id = Column(Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True)
    group_title = Column(String(512), nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    player_username = Column(String(255), nullable=True)
    gg_player_id = Column(String(255), nullable=True)
    player_details_id = Column(
        Integer,
        ForeignKey("player_details.id", ondelete="SET NULL"),
        nullable=True,
    )
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(32), nullable=False, server_default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)

    club = relationship("Club")
    player_details = relationship("PlayerDetails")


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


class MtProtoClubHealth(Base):
    """Worker-reported Telethon live status per club (Dashboard reads; no web-side connect)."""

    __tablename__ = "mtproto_club_health"

    club_key = Column(String(64), primary_key=True)
    worker_connected = Column(Boolean, nullable=False, default=False)
    session_valid = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="unknown")
    status_detail = Column(Text, nullable=True)
    telegram_user_id = Column(BigInteger, nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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


class StaffCashoutRecord(Base):
    """Completed GGCashier cashouts — editable audit record separate from job workflow."""

    __tablename__ = "staff_cashout_records"
    __table_args__ = (
        UniqueConstraint("cashier_job_id", name="uq_staff_cashout_records_cashier_job_id"),
        Index("ix_staff_cashout_records_club_id", "club_id"),
        Index("ix_staff_cashout_records_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    cashier_job_id = Column(
        Integer,
        ForeignKey("cashier_cashout_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    chat_id = Column(BigInteger, nullable=False)
    group_title = Column(String(255), nullable=False)
    gg_player_id = Column(String(64), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    recorded_by_telegram_user_id = Column(BigInteger, nullable=False)
    trigger = Column(String(20), nullable=False)  # group_cash | dm_cashout
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    club = relationship("Club")
    cashier_job = relationship("CashierCashoutJob")
    payments = relationship(
        "StaffCashoutPayment",
        back_populates="cashout_record",
        cascade="all, delete-orphan",
        order_by="StaffCashoutPayment.sort_order",
    )


class StaffCashoutPayment(Base):
    """Payout line(s) for a staff cashout record — supports multiple payment methods."""

    __tablename__ = "staff_cashout_payments"
    __table_args__ = (
        Index("ix_staff_cashout_payments_record_id", "cashout_record_id"),
    )

    id = Column(Integer, primary_key=True)
    cashout_record_id = Column(
        Integer,
        ForeignKey("staff_cashout_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_method_id = Column(Integer, nullable=True)
    payment_sub_option_id = Column(Integer, nullable=True)
    method_display_name = Column(String(100), nullable=True)
    payout_details = Column(Text, nullable=True)
    amount = Column(Numeric(12, 2), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    cashout_record = relationship("StaffCashoutRecord", back_populates="payments")


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


class MigratedGroupRecovery(Base):
    """Queue for one-shot direct-add recovery after basic-group → supergroup migration."""

    __tablename__ = "migrated_group_recovery"
    __table_args__ = (
        Index("ix_migrated_group_recovery_claim", "readd_status", "priority_tier", "priority_rank"),
        Index("ix_migrated_group_recovery_club_key", "club_key"),
        UniqueConstraint("telegram_chat_id", name="uq_migrated_group_recovery_telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_key = Column(String(64), nullable=False)
    club_id = Column(Integer, nullable=False)
    group_title = Column(Text, nullable=False)
    old_chat_id = Column(BigInteger, nullable=False)
    player_telegram_user_id = Column(BigInteger, nullable=True)
    player_username = Column(Text, nullable=True)
    player_display_name = Column(Text, nullable=True)
    priority_tier = Column(Integer, nullable=False)
    priority_rank = Column(BigInteger, nullable=False, default=0)
    readd_status = Column(String(32), nullable=False, default="pending")
    readd_result = Column(JSONB, nullable=True)
    invite_link = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    readd_attempted_at = Column(DateTime(timezone=True), nullable=True)
    readd_completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class MigrationRecoveryControl(Base):
    """Singleton row: auto-disable state for migration recovery worker cron."""

    __tablename__ = "migration_recovery_control"

    id = Column(Integer, primary_key=True, default=1)
    auto_disabled_at = Column(DateTime(timezone=True), nullable=True)
    auto_disabled_reason = Column(Text, nullable=True)
    exhausted_club_key = Column(String(64), nullable=True)
    pending_snapshot = Column(JSONB, nullable=True)
    last_tick_at = Column(DateTime(timezone=True), nullable=True)
    last_slack_summary_at = Column(DateTime(timezone=True), nullable=True)
    rate_limit_resume_at = Column(DateTime(timezone=True), nullable=True)
    club_rate_limit_resume_at = Column(JSONB, nullable=True)


class InactiveGroupOutreachControl(Base):
    """Singleton row: one-shot inactive group outreach scan state."""

    __tablename__ = "inactive_group_outreach_control"

    id = Column(Integer, primary_key=True, default=1)
    scan_status = Column(String(32), nullable=False, default="idle")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    targets_total = Column(Integer, nullable=False, default=0)
    rows_scanned = Column(Integer, nullable=False, default=0)
    inactive_90d_count = Column(Integer, nullable=False, default=0)
    inactive_180d_count = Column(Integer, nullable=False, default=0)
    entity_resolvable_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    last_tick_at = Column(DateTime(timezone=True), nullable=True)
    dm_campaign_message = Column(Text, nullable=True)
    dm_batch_status = Column(String(32), nullable=True)
    dm_campaign_started_at = Column(DateTime(timezone=True), nullable=True)
    dm_campaign_started_by_telegram_user_id = Column(BigInteger, nullable=True)
    dm_sent_count = Column(Integer, nullable=False, default=0)
    dm_failed_count = Column(Integer, nullable=False, default=0)


class InactiveGroupOutreachRow(Base):
    """Per-megagroup audit row for inactive outreach scan and manual staging."""

    __tablename__ = "inactive_group_outreach_rows"
    __table_args__ = (
        UniqueConstraint(
            "club_key",
            "telegram_chat_id",
            name="uq_inactive_group_outreach_club_chat",
        ),
        Index("ix_inactive_group_outreach_rows_scan_status", "scan_status", "club_key"),
        Index(
            "ix_inactive_group_outreach_rows_stage_status",
            "stage_status",
            "club_key",
        ),
        Index(
            "ix_inactive_group_outreach_rows_dm_lookup",
            "club_key",
            "player_telegram_user_id",
            "dm_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    club_key = Column(String(64), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    group_title = Column(Text, nullable=False)
    legacy_chat_id = Column(BigInteger, nullable=True)
    gg_player_id = Column(String(64), nullable=True)
    last_external_message_at = Column(DateTime(timezone=True), nullable=True)
    activity_basis = Column(String(32), nullable=True)
    last_external_supergroup_at = Column(DateTime(timezone=True), nullable=True)
    activity_basis_supergroup = Column(String(32), nullable=True)
    last_external_legacy_at = Column(DateTime(timezone=True), nullable=True)
    activity_basis_legacy = Column(String(32), nullable=True)
    activity_merged_from = Column(String(16), nullable=True)
    inactive_90d = Column(Boolean, nullable=False, default=False)
    inactive_180d = Column(Boolean, nullable=False, default=False)
    duplicate_title = Column(Boolean, nullable=False, default=False)
    newer_same_title_chat_id = Column(BigInteger, nullable=True)
    player_telegram_user_id = Column(BigInteger, nullable=True)
    player_username = Column(Text, nullable=True)
    player_display_name = Column(Text, nullable=True)
    player_source = Column(String(32), nullable=True)
    account_check = Column(String(16), nullable=True)
    entity_resolvable = Column(Boolean, nullable=False, default=False)
    scan_status = Column(String(16), nullable=False, default="pending")
    scan_error = Column(Text, nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    dm_status = Column(String(32), nullable=True)
    dm_error = Column(Text, nullable=True)
    dm_sent_at = Column(DateTime(timezone=True), nullable=True)
    stage_status = Column(String(32), nullable=True)
    staged_at = Column(DateTime(timezone=True), nullable=True)
    staged_by_telegram_user_id = Column(BigInteger, nullable=True)
    stage_note = Column(Text, nullable=True)
    reply_received_at = Column(DateTime(timezone=True), nullable=True)
    reonboard_new_chat_id = Column(BigInteger, nullable=True)
    reonboard_error = Column(Text, nullable=True)
    old_group_erased_at = Column(DateTime(timezone=True), nullable=True)
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
        Index("ix_stripe_checkout_deposit_session_id", "deposit_session_id"),
    )

    id = Column(Integer, primary_key=True)
    deposit_session_id = Column(String(64), nullable=True)
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
    """Remember bind candidates: normalized payer name + support group (any shared Venmo)."""

    __tablename__ = "venmo_payer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "payer_name_normalized",
            "telegram_chat_id",
            name="uq_venmo_payer_bindings_payer_chat",
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


class CashAppPayment(Base):
    """One Cash App deposit ingested from Zapier; optionally bound to a support group."""

    __tablename__ = "cashapp_payments"
    __table_args__ = (
        Index(
            "ix_cashapp_payments_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_cashapp_payments_telegram_chat_id", "telegram_chat_id"),
        Index("ix_cashapp_payments_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    payer_name = Column(String(255), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    cashapp_handle = Column(String(100), nullable=False)
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


class CashAppPayerBinding(Base):
    """Remember bind candidates: normalized payer name + support group (any shared Cash App)."""

    __tablename__ = "cashapp_payer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "payer_name_normalized",
            "telegram_chat_id",
            name="uq_cashapp_payer_bindings_payer_chat",
        ),
        Index("ix_cashapp_payer_bindings_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    payer_name_normalized = Column(String(255), nullable=False)
    cashapp_handle = Column(String(100), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    last_bound_at = Column(DateTime(timezone=True), server_default=func.now())
    last_bound_by_telegram_user_id = Column(BigInteger, nullable=True)

    club = relationship("Club")


class PayPalPayment(Base):
    """One PayPal deposit ingested from Zapier; optionally bound to a support group."""

    __tablename__ = "paypal_payments"
    __table_args__ = (
        Index(
            "ix_paypal_payments_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_paypal_payments_telegram_chat_id", "telegram_chat_id"),
        Index("ix_paypal_payments_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    payer_name = Column(String(255), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    paypal_email = Column(String(255), nullable=False)
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


class PayPalPayerBinding(Base):
    """Remember bind candidates: normalized payer name + support group (any shared PayPal)."""

    __tablename__ = "paypal_payer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "payer_name_normalized",
            "telegram_chat_id",
            name="uq_paypal_payer_bindings_payer_chat",
        ),
        Index("ix_paypal_payer_bindings_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    payer_name_normalized = Column(String(255), nullable=False)
    paypal_email = Column(String(255), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    last_bound_at = Column(DateTime(timezone=True), server_default=func.now())
    last_bound_by_telegram_user_id = Column(BigInteger, nullable=True)

    club = relationship("Club")


class ZellePayment(Base):
    """One Zelle deposit ingested from Zapier; optionally bound to a support group."""

    __tablename__ = "zelle_payments"
    __table_args__ = (
        Index(
            "ix_zelle_payments_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_zelle_payments_telegram_chat_id", "telegram_chat_id"),
        Index("ix_zelle_payments_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    payer_name = Column(String(255), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    zelle_recipient = Column(String(100), nullable=False)
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


class CryptoPayment(Base):
    """One on-chain deposit ingested from Arkham/Zapier; bound to a support group."""

    __tablename__ = "crypto_payments"
    __table_args__ = (
        Index(
            "ix_crypto_payments_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_crypto_payments_telegram_chat_id", "telegram_chat_id"),
        Index("ix_crypto_payments_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    amount_cents = Column(Integer, nullable=False)
    token_symbol = Column(String(32), nullable=False)
    token_name = Column(String(100), nullable=True)
    chain = Column(String(32), nullable=False)
    from_address = Column(String(255), nullable=False)
    from_entity_name = Column(String(255), nullable=True)
    to_address = Column(String(255), nullable=False)
    transaction_hash = Column(String(255), nullable=False)
    paid_at = Column(String(255), nullable=True)
    source_external_id = Column(String(255), unique=True, nullable=True)
    alert_name = Column(String(255), nullable=True)
    alert_scope = Column(String(32), nullable=False)
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")


class CryptoWalletBinding(Base):
    """Remember bind candidates: wallet address + alert scope + support group."""

    __tablename__ = "crypto_wallet_bindings"
    __table_args__ = (
        UniqueConstraint(
            "from_address_normalized",
            "alert_scope",
            "telegram_chat_id",
            name="uq_crypto_wallet_bindings_address_scope_chat",
        ),
        Index("ix_crypto_wallet_bindings_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    from_address_normalized = Column(String(255), nullable=False)
    alert_scope = Column(String(32), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    last_bound_at = Column(DateTime(timezone=True), server_default=func.now())
    last_bound_by_telegram_user_id = Column(BigInteger, nullable=True)

    club = relationship("Club")


class ZellePayerBinding(Base):
    """Remember bind candidates: normalized payer name + support group (any shared Zelle)."""

    __tablename__ = "zelle_payer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "payer_name_normalized",
            "telegram_chat_id",
            name="uq_zelle_payer_bindings_payer_chat",
        ),
        Index("ix_zelle_payer_bindings_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    payer_name_normalized = Column(String(255), nullable=False)
    zelle_recipient = Column(String(100), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title_at_bind = Column(String(255), nullable=True)
    last_bound_at = Column(DateTime(timezone=True), server_default=func.now())
    last_bound_by_telegram_user_id = Column(BigInteger, nullable=True)

    club = relationship("Club")


class PaymentAutoDepositEvent(Base):
    """E2E auto-deposit outcome for a single ingested payment (forward-only analytics)."""

    __tablename__ = "payment_auto_deposit_events"
    __table_args__ = (
        UniqueConstraint(
            "payment_method_slug",
            "payment_id",
            name="uq_pade_method_payment",
        ),
        Index("ix_pade_club_payment_at", "club_id", "payment_at"),
        Index("ix_pade_method_payment_at", "payment_method_slug", "payment_at"),
        Index("ix_pade_status", "status"),
        Index("ix_pade_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    payment_method_slug = Column(String(32), nullable=False)
    payment_id = Column(Integer, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    telegram_chat_id = Column(BigInteger, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    auto_bound = Column(Boolean, nullable=False, default=False)
    goods_or_services = Column(Boolean, nullable=False, default=False)
    group_title = Column(String(255), nullable=True)
    gg_player_id = Column(String(64), nullable=True)
    club_auto_deposit_enabled = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False)
    skip_reason = Column(String(64), nullable=True)
    chip_add_status = Column(String(32), nullable=True)
    payment_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())

    club = relationship("Club")


class BotFlowSession(Base):
    """One active deposit/cashout flow per support group chat (UUID lifecycle)."""

    __tablename__ = "bot_flow_sessions"
    __table_args__ = (
        Index("ix_bfs_chat_status", "telegram_chat_id", "status"),
        Index("ix_bfs_flow_type_status", "flow_type", "status"),
        Index(
            "uq_bfs_active_chat",
            "telegram_chat_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    session_uuid = Column(String(64), primary_key=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    flow_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    telegram_user_id = Column(BigInteger, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    end_reason = Column(String(32), nullable=True)

    club = relationship("Club")


class GroupChatDailyActivity(Base):
    """Daily rollup of non-bot messages in club-linked support groups."""

    __tablename__ = "group_chat_daily_activity"
    __table_args__ = (
        UniqueConstraint(
            "activity_date",
            "chat_id",
            name="uq_gcda_activity_date_chat_id",
        ),
        Index("ix_gcda_club_activity_date", "club_id", "activity_date"),
        Index("ix_gcda_activity_date", "activity_date"),
    )

    id = Column(Integer, primary_key=True)
    activity_date = Column(Date, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    non_bot_message_count = Column(Integer, nullable=False, default=1)
    first_message_at = Column(DateTime(timezone=True), nullable=False)
    last_message_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")


class GroupChatDailyTranscript(Base):
    """Previous-day support-group conversation blob (MTProto nightly extract)."""

    __tablename__ = "group_chat_daily_transcripts"
    __table_args__ = (
        UniqueConstraint(
            "activity_date",
            "chat_id",
            name="uq_gcdt_activity_date_chat_id",
        ),
        Index("ix_gcdt_club_activity_date", "club_id", "activity_date"),
        Index("ix_gcdt_activity_date", "activity_date"),
        Index("ix_gcdt_status", "status"),
        Index("ix_gcdt_analysis_status", "analysis_status"),
        Index("ix_gcdt_activity_date_analysis_status", "activity_date", "analysis_status"),
    )

    id = Column(Integer, primary_key=True)
    activity_date = Column(Date, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(16), nullable=False, default="pending")
    message_count = Column(Integer, nullable=False, default=0)
    messages = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    analysis_status = Column(String(16), nullable=False, default="pending")
    analysis_error = Column(Text, nullable=True)
    analysis_attempt_count = Column(Integer, nullable=False, default=0)
    analyzed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")


class GroupChatTicket(Base):
    """Segmented + classified support ticket for one chat-day."""

    __tablename__ = "group_chat_tickets"
    __table_args__ = (
        UniqueConstraint(
            "activity_date",
            "chat_id",
            "ticket_index",
            name="uq_gct_activity_date_chat_id_ticket_index",
        ),
        Index("ix_gct_club_activity_date", "club_id", "activity_date"),
        Index("ix_gct_activity_date", "activity_date"),
        Index("ix_gct_category", "category"),
    )

    id = Column(Integer, primary_key=True)
    activity_date = Column(Date, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    ticket_index = Column(Integer, nullable=False)
    start_msg_id = Column(BigInteger, nullable=False)
    end_msg_id = Column(BigInteger, nullable=False)
    message_ids = Column(JSONB, nullable=False)
    brief_summary = Column(Text, nullable=True)
    category = Column(String(32), nullable=False)
    events = Column(JSONB, nullable=True)
    summary = Column(Text, nullable=True)
    prompt_version = Column(String(32), nullable=False)
    model = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")


class DepositFunnelEvent(Base):
    """Append-only /deposit → chips funnel step (one row per session + step)."""

    __tablename__ = "deposit_funnel_events"
    __table_args__ = (
        UniqueConstraint(
            "deposit_session_id",
            "step",
            name="uq_dfe_session_step",
        ),
        Index("ix_dfe_club_created_at", "club_id", "created_at"),
        Index("ix_dfe_chat_created_at", "telegram_chat_id", "created_at"),
        Index("ix_dfe_step_created_at", "step", "created_at"),
        Index("ix_dfe_session_id", "deposit_session_id"),
    )

    id = Column(Integer, primary_key=True)
    deposit_session_id = Column(String(64), nullable=False)
    step = Column(String(64), nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    telegram_user_id = Column(BigInteger, nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    method_slug = Column(String(32), nullable=True)
    amount_cents = Column(Integer, nullable=True)
    is_first_deposit = Column(Boolean, nullable=False, default=False)
    requires_method_setup = Column(Boolean, nullable=False, default=False)
    metadata_json = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
        Index("ix_pmba_deposit_session_id", "deposit_session_id"),
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
    deposit_session_id = Column(String(64), nullable=True)
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
    zelle_payment_id = Column(
        Integer,
        ForeignKey("zelle_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    cashapp_payment_id = Column(
        Integer,
        ForeignKey("cashapp_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    paypal_payment_id = Column(
        Integer,
        ForeignKey("paypal_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    club = relationship("Club")
    variant = relationship("ClubPaymentTierVariant")
    venmo_payment = relationship("VenmoPayment")
    zelle_payment = relationship("ZellePayment")
    cashapp_payment = relationship("CashAppPayment")
    paypal_payment = relationship("PayPalPayment")


class GroupDepositMethodAccess(Base):
    """Per-support-group blacklist or whitelist for a deposit ClubPaymentMethod."""

    __tablename__ = "group_deposit_method_access"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "club_payment_method_id",
            name="uq_gdma_chat_method",
        ),
        CheckConstraint(
            "access_type IN ('blacklist', 'whitelist')",
            name="ck_gdma_access_type",
        ),
        Index("ix_gdma_telegram_chat_id", "telegram_chat_id"),
        Index("ix_gdma_club_id", "club_id"),
        Index("ix_gdma_method_id", "club_payment_method_id"),
    )

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    club_payment_method_id = Column(
        Integer,
        ForeignKey("club_payment_methods.id", ondelete="CASCADE"),
        nullable=False,
    )
    access_type = Column(String(16), nullable=False)
    created_by_telegram_user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    club = relationship("Club")
    method = relationship(
        "ClubPaymentMethod", back_populates="deposit_method_access"
    )


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


class PaymentBindingEvent(Base):
    """Append-only audit log for payment/group binding and notification sync."""

    __tablename__ = "payment_binding_events"
    __table_args__ = (
        Index(
            "ix_pbe_method_payment",
            "payment_method_slug",
            "payment_id",
        ),
        Index(
            "ix_pbe_notification_msg",
            "notification_chat_id",
            "notification_message_id",
        ),
        Index("ix_pbe_event_type_created", "event_type", "created_at"),
        Index("ix_pbe_telegram_chat_id", "telegram_chat_id"),
    )

    id = Column(Integer, primary_key=True)
    event_type = Column(String(32), nullable=False)
    payment_method_slug = Column(String(32), nullable=False)
    payment_id = Column(Integer, nullable=True)
    bind_attempt_id = Column(
        Integer,
        ForeignKey("payment_method_bind_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    group_binding_id = Column(
        Integer,
        ForeignKey("group_payment_method_bindings.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_chat_id = Column(BigInteger, nullable=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True
    )
    bound_group_title = Column(String(255), nullable=True)
    bound_via = Column(String(32), nullable=True)
    auto_bound = Column(Boolean, nullable=True)
    actor_telegram_user_id = Column(BigInteger, nullable=True)
    notification_chat_id = Column(BigInteger, nullable=True)
    notification_message_id = Column(BigInteger, nullable=True)
    previous_telegram_chat_id = Column(BigInteger, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    club = relationship("Club")
    bind_attempt = relationship("PaymentMethodBindAttempt")
    group_binding = relationship("GroupPaymentMethodBinding")


class IssueReport(Base):
    """Account-manager issue report ticket."""

    __tablename__ = "issue_reports"
    __table_args__ = (Index("ix_issue_reports_created_at", "created_at"),)

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(ARRAY(String(32)), nullable=False, server_default="{}")
    category = Column(String(32), nullable=True)
    notify_tags = Column(ARRAY(String(32)), nullable=False, server_default="{}")
    status = Column(String(32), nullable=False, server_default="open")
    reporter_name = Column(String(255), nullable=True)
    reporter_source = Column(String(32), nullable=False, server_default="api")
    reporter_telegram_user_id = Column(BigInteger, nullable=True)
    club_id = Column(Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True)
    group_title = Column(String(512), nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    slack_message_ts = Column(String(64), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    last_slack_reminder_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_telegram_user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")
    attachments = relationship(
        "IssueReportAttachment",
        back_populates="issue_report",
        cascade="all, delete-orphan",
    )


class IssueReportAttachment(Base):
    """Screenshot attached to an issue report."""

    __tablename__ = "issue_report_attachments"

    id = Column(Integer, primary_key=True)
    issue_report_id = Column(
        Integer,
        ForeignKey("issue_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=False)
    content = Column(LargeBinary, nullable=False)
    attachment_type = Column(String(32), nullable=False, server_default="evidence")
    slack_file_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    issue_report = relationship("IssueReport", back_populates="attachments")


class IssueReportDraft(Base):
    """Pending issue report started from a support group /report command."""

    __tablename__ = "issue_report_drafts"
    __table_args__ = (
        Index("ix_issue_report_drafts_staff_user_id", "staff_telegram_user_id"),
        Index("ix_issue_report_drafts_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    staff_telegram_user_id = Column(BigInteger, nullable=False)
    club_id = Column(Integer, ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True)
    group_title = Column(String(512), nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    status = Column(String(32), nullable=False, server_default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)

    club = relationship("Club")


class PlayerSupportIssue(Base):
    """Open or resolved player dispute tracked between AM shifts."""

    __tablename__ = "player_support_issues"
    __table_args__ = (
        Index("ix_player_support_issues_club_id", "club_id"),
        Index("ix_player_support_issues_gg_player_id", "gg_player_id"),
        Index("ix_player_support_issues_status", "status"),
        Index(
            "uq_player_support_issues_open_club_player",
            "club_id",
            "gg_player_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    gg_player_id = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, server_default="open")
    telegram_chat_id = Column(BigInteger, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_telegram_user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    club = relationship("Club")
    notes = relationship(
        "PlayerSupportNote",
        back_populates="issue",
        cascade="all, delete-orphan",
        order_by="PlayerSupportNote.created_at",
    )


class PlayerSupportNote(Base):
    """Append-only dispute note on a player support issue."""

    __tablename__ = "player_support_notes"
    __table_args__ = (
        Index("ix_player_support_notes_issue_id", "issue_id"),
        Index("ix_player_support_notes_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    issue_id = Column(
        Integer,
        ForeignKey("player_support_issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    situation = Column(Text, nullable=False)
    actions_taken = Column(Text, nullable=False)
    next_steps = Column(Text, nullable=False)
    created_by_telegram_user_id = Column(BigInteger, nullable=False)
    source_telegram_chat_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    issue = relationship("PlayerSupportIssue", back_populates="notes")


class TradeRecordUpload(Base):
    """One trade record XLSX ingest per club slug + local audit day."""

    __tablename__ = "trade_record_uploads"
    __table_args__ = (
        UniqueConstraint(
            "club_slug",
            "audit_date",
            name="uq_trade_record_uploads_slug_date",
        ),
        Index("ix_trade_record_uploads_club_id", "club_id"),
        Index("ix_trade_record_uploads_club_slug", "club_slug"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    club_slug = Column(String(64), nullable=True)
    audit_timezone_policy = Column(String(32), nullable=True)
    audit_date = Column(Date, nullable=False)
    filename = Column(String(512), nullable=False)
    metadata_json = Column(Text, nullable=True)
    replaced_upload_id = Column(
        Integer,
        ForeignKey("trade_record_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club")
    lines = relationship(
        "TradeRecordLine",
        back_populates="upload",
        cascade="all, delete-orphan",
    )


class TradeRecordLine(Base):
    """Parsed chip movement line from a trade record upload."""

    __tablename__ = "trade_record_lines"
    __table_args__ = (
        Index("ix_trade_record_lines_upload_id", "upload_id"),
        Index("ix_trade_record_lines_member_gg_player_id", "member_gg_player_id"),
    )

    id = Column(Integer, primary_key=True)
    upload_id = Column(
        Integer,
        ForeignKey("trade_record_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )
    sheet_row = Column(Integer, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    member_gg_player_id = Column(String(255), nullable=True)
    member_nickname = Column(String(255), nullable=True)
    agent_gg_player_id = Column(String(255), nullable=True)
    super_agent_gg_player_id = Column(String(255), nullable=True)

    upload = relationship("TradeRecordUpload", back_populates="lines")


class EarlyRakebackSnapshot(Base):
    """One early-rakeback sync per club slug + local audit day (re-sync replaces)."""

    __tablename__ = "early_rakeback_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "club_slug",
            "audit_date",
            name="uq_early_rakeback_snapshots_slug_date",
        ),
        Index("ix_early_rakeback_snapshots_club_id", "club_id"),
        Index("ix_early_rakeback_snapshots_club_slug", "club_slug"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    club_slug = Column(String(64), nullable=False)
    audit_date = Column(Date, nullable=False)
    fetch_from_utc = Column(DateTime(timezone=True), nullable=False)
    fetch_to_utc = Column(DateTime(timezone=True), nullable=False)
    lines_fetched = Column(Integer, nullable=False, default=0)
    lines_stored = Column(Integer, nullable=False, default=0)
    lines_skipped_unmapped = Column(Integer, nullable=False, default=0)
    skipped_nicknames = Column(Text, nullable=True)
    synced_at = Column(DateTime, server_default=func.now())

    club = relationship("Club")
    lines = relationship(
        "EarlyRakebackLine",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class EarlyRakebackLine(Base):
    """One early-rakeback payout record with resolved gg_player_id."""

    __tablename__ = "early_rakeback_lines"
    __table_args__ = (
        Index("ix_early_rakeback_lines_snapshot_id", "snapshot_id"),
        Index("ix_early_rakeback_lines_gg_player_id", "gg_player_id"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(
        Integer,
        ForeignKey("early_rakeback_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_entry_id = Column(String(64), nullable=False)
    source_record_id = Column(String(64), nullable=False)
    gg_player_id = Column(String(255), nullable=False)
    member_nickname = Column(String(255), nullable=True)
    member_type = Column(String(32), nullable=True)
    amount_usd = Column(Numeric(14, 2), nullable=False)
    rake = Column(Numeric(14, 2), nullable=True)
    pl = Column(Numeric(14, 2), nullable=True)
    rakeback_percentage = Column(Numeric(8, 4), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)

    snapshot = relationship("EarlyRakebackSnapshot", back_populates="lines")


class AuditReconcileRun(Base):
    """One net reconcile run per club slug + local audit day (re-run replaces)."""

    __tablename__ = "audit_reconcile_runs"
    __table_args__ = (
        UniqueConstraint(
            "club_slug",
            "audit_date",
            name="uq_audit_reconcile_runs_slug_date",
        ),
        Index("ix_audit_reconcile_runs_club_id", "club_id"),
        Index("ix_audit_reconcile_runs_club_slug", "club_slug"),
        Index("ix_audit_reconcile_runs_audit_date", "audit_date"),
    )

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    club_slug = Column(String(64), nullable=False)
    audit_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False)
    trade_upload_id = Column(
        Integer,
        ForeignKey("trade_record_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )
    early_rb_snapshot_id = Column(
        Integer,
        ForeignKey("early_rakeback_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    players_matched = Column(Integer, nullable=False, default=0)
    players_failed = Column(Integer, nullable=False, default=0)
    unmatched_trade_count = Column(Integer, nullable=False, default=0)
    unmatched_ledger_count = Column(Integer, nullable=False, default=0)
    report_json = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club")
    trade_upload = relationship("TradeRecordUpload")
    early_rb_snapshot = relationship("EarlyRakebackSnapshot")


class GlideAuditLine(Base):
    """Optional snapshot of Glide RT Hub rows used in reconcile."""

    __tablename__ = "glide_audit_lines"
    __table_args__ = (
        Index("ix_glide_audit_lines_club_slug", "club_slug"),
        Index("ix_glide_audit_lines_audit_date", "audit_date"),
    )

    id = Column(Integer, primary_key=True)
    club_slug = Column(String(64), nullable=False)
    audit_date = Column(Date, nullable=False)
    glide_row_id = Column(String(128), nullable=False)
    gg_player_id = Column(String(255), nullable=True)
    amount_usd = Column(Numeric(14, 2), nullable=False)
    event_type = Column(String(64), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
