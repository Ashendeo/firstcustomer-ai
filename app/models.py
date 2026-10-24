import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
    seller_name: Mapped[str] = mapped_column(String(200), default="")
    seller_domain: Mapped[str] = mapped_column(String(180), default="")
    product: Mapped[str] = mapped_column(Text)
    ideal_customer: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(60), default="India")
    buyer_titles: Mapped[str] = mapped_column(Text, default="Head of HR,HR Director")
    min_headcount: Mapped[int] = mapped_column(Integer, default=0)
    min_revenue_usd: Mapped[int] = mapped_column(Integer, default=0)
    max_headcount: Mapped[int] = mapped_column(Integer, default=0)
    max_revenue_usd: Mapped[int] = mapped_column(Integer, default=0)
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
    discovery_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_id: Mapped[str] = mapped_column(String(80), default="")
    company_name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(180), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    person_id: Mapped[str] = mapped_column(String(80), default="")
    person_name: Mapped[str] = mapped_column(String(160), default="")
    person_title: Mapped[str] = mapped_column(String(180), default="")
    person_profile: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
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
    __table_args__ = (UniqueConstraint("campaign_id", "discovery_key", name="uq_leads_campaign_discovery"),)
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


class StepCheckpoint(Base):
    __tablename__ = "step_checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    step_key: Mapped[str] = mapped_column(String(160))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="in_flight")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("campaign_id", "step_key", name="uq_checkpoint_campaign_step"),)


class OutreachPolicy(Base):
    """Campaign authorization: auto_send is a preference, live requires opt-in."""
    __tablename__ = "outreach_policies"
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score: Mapped[int] = mapped_column(Integer, default=75)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    prepare_linkedin_drafts: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(24), default="simulation")
    legal_basis: Mapped[str] = mapped_column(String(45), default="")
    organization_type: Mapped[str] = mapped_column(String(45), default="")
    sender_postal_address: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[str] = mapped_column(String(254), default="")
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Suppression(Base):
    __tablename__ = "outreach_suppressions"
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutreachAttempt(Base):
    __tablename__ = "outreach_attempts"
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    day_utc: Mapped[str] = mapped_column(String(10), index=True)
    day_slot: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("day_utc", "day_slot", name="uq_outreach_global_day_slot"),
        UniqueConstraint("email", name="uq_outreach_first_contact_email"),
    )
    state: Mapped[str] = mapped_column(String(24), default="reserved")
    provider_id: Mapped[str] = mapped_column(String(150), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessedResendEvent(Base):
    __tablename__ = "processed_resend_events"
    event_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InboundReply(Base):
    __tablename__ = "inbound_replies"
    message_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(254))
    classification: Mapped[str] = mapped_column(String(45))
    excerpt: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MarketPlaybook(Base):
    """Shared seller-domain market research and reusable buyer-fit skill.

    A domain is researched once across campaigns. The initial in_flight row is
    committed before invoking paid providers; uncertain work is not replayed.
    """
    __tablename__ = "market_playbooks"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_domain: Mapped[str] = mapped_column(String(180), index=True)
    product_hash: Mapped[str] = mapped_column(String(64))
    market_domain: Mapped[str] = mapped_column(String(90), default="")
    status: Mapped[str] = mapped_column(String(24), default="in_flight")
    research: Mapped[dict] = mapped_column(JSON, default=dict)
    rubric: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SellerLookup(Base):
    """Global write-ahead journal for paid pre-campaign company name lookup."""
    __tablename__ = "seller_lookups"
    lookup_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    normalized_query: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    results: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
