import enum
import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import JSON, String, Text, Float, ForeignKey, DateTime, Enum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

class Base(DeclarativeBase):
    pass

# Enums for the Crowdsourced Outage Map
class UtilityType(str, enum.Enum):
    STEG = "STEG"
    SONEDE = "SONEDE"
    OTHER = "OTHER"

class ReportStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    RESOLVED = "RESOLVED"

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Nullable so anonymous/demo users (no credentials) can coexist with
    # registered accounts. Registration enforces email+password presence.
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    # lazy="selectin" keeps eager loading async-safe (avoids MissingGreenlet in async endpoints)
    conversations: Mapped[List["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    outage_reports: Mapped[List["OutageReport"]] = relationship(
        back_populates="user", lazy="selectin"
    )

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="conversations", lazy="selectin")
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    
    # Stores retrieved RAG document metadata (PDF titles, pages, chunks)
    # JSONB on Postgres (the production backend), generic JSON elsewhere (SQLite tests).
    sources: Mapped[Optional[dict]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages", lazy="selectin"
    )

class Setting(Base):
    """Runtime configuration (key/value) editable by admins.

    Settings override environment defaults at runtime (e.g. purge TTL,
    purge interval, map refresh cadence). Values are stored as strings and
    parsed where needed. Only admin-authorized endpoints can read/write them.
    """
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutageReport(Base):
    __tablename__ = "outage_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    utility: Mapped[UtilityType] = mapped_column(
        Enum(UtilityType), default=UtilityType.STEG, nullable=False
    )
    region: Mapped[str] = mapped_column(String(100), index=True, nullable=False)  # e.g., "Sousse", "Sfax", "Tozeur"
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus), default=ReportStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        back_populates="outage_reports", lazy="selectin"
    )