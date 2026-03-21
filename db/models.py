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
)
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
    created_at = Column(DateTime, server_default=func.now())

    club = relationship("Club", back_populates="payment_methods")
    sub_options = relationship(
        "PaymentSubOption", back_populates="method", cascade="all, delete-orphan"
    )
    tiers = relationship(
        "PaymentMethodTier", back_populates="method", cascade="all, delete-orphan",
        order_by="PaymentMethodTier.sort_order",
    )


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


class Group(Base):
    __tablename__ = "groups"

    chat_id = Column(BigInteger, primary_key=True)
    club_id = Column(
        Integer, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False
    )
    added_at = Column(DateTime, server_default=func.now())

    club = relationship("Club", back_populates="groups")


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
