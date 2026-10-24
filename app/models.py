import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(140))
    product: Mapped[str] = mapped_column(Text)
    ideal_customer: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(60), default="India")
    buyer_titles: Mapped[str] = mapped_column(Text, default="Head of HR,HR Director")
    min_headcount: Mapped[int] = mapped_column(Integer, default=0)
    max_headcount: Mapped[int] = mapped_column(Integer, default=0)
    booking_url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    error: Mapped[str] = mapped_column(Text, default="")
    search_query: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    leads: Mapped[list["Lead"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(80), default="")
    company_name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(180), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    person_id: Mapped[str] = mapped_column(String(80), default="")
    person_name: Mapped[str] = mapped_column(String(160), default="")
    person_title: Mapped[str] = mapped_column(String(180), default="")
    person_profile: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40), default="crustdata")
    draft_subject: Mapped[str] = mapped_column(Text, default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    provider_message_id: Mapped[str] = mapped_column(String(150), default="")
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booking_event_uri: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    campaign: Mapped[Campaign] = relationship(back_populates="leads")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_id: Mapped[str] = mapped_column(String(36), default="")
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"
    event_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
