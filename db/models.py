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
    sort_order = Column(Integer, default=0)

    method = relationship("PaymentMethod", back_populates="tiers")
    variants = relationship(
        "MethodVariant", back_populates="tier", cascade="all, delete-orphan",
        order_by="MethodVariant.sort_order",
    )


class Group(Base):
    __tablename__ = "groups"

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
    """Admin-granted cooldown bypasses for specific players."""

    __tablename__ = "cooldown_bypasses"

    id = Column(Integer, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id = Column(BigInteger, nullable=False)
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


class SupportGroupChat(Base):
    """Megagroups created via /gc MTProto automation (per-club sessions)."""

    __tablename__ = "support_group_chats"
    __table_args__ = (
        Index("ix_support_group_chats_club_key", "club_key"),
        Index("ix_support_group_chats_telegram_chat_id", "telegram_chat_id"),
        Index("ix_support_group_chats_created_by", "created_by_telegram_user_id"),
        Index("ix_support_group_chats_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    club_key = Column(String(64), nullable=False)
    club_display_name = Column(String(255), nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    telegram_chat_title = Column(Text, nullable=False)
    invite_link = Column(Text, nullable=True)
    created_by_telegram_user_id = Column(BigInteger, nullable=False)
    mtproto_session_name = Column(Text, nullable=True)
    added_users = Column(JSONB, nullable=True)
    failed_users = Column(JSONB, nullable=True)
    group_photo_path = Column(Text, nullable=True)
    initial_message_sent = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
