"""Initial campaign, lead and audit schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("ideal_customer", sa.Text(), nullable=False),
        sa.Column("country", sa.String(60), nullable=False),
        sa.Column("buyer_titles", sa.Text(), nullable=False),
        sa.Column("min_headcount", sa.Integer(), nullable=False),
        sa.Column("max_headcount", sa.Integer(), nullable=False),
        sa.Column("booking_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(80), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("person_id", sa.String(80), nullable=False),
        sa.Column("person_name", sa.String(160), nullable=False),
        sa.Column("person_title", sa.String(180), nullable=False),
        sa.Column("person_profile", sa.Text(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=False),
        sa.Column("fit_reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("draft_subject", sa.Text(), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("provider_message_id", sa.String(150), nullable=False),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booking_event_uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_leads_campaign_id", "leads", ["campaign_id"])
    op.create_table("audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("lead_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_campaign_id", "audit_events", ["campaign_id"])
    op.create_table("processed_webhooks",
        sa.Column("event_key", sa.String(160), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("processed_webhooks")
    op.drop_index("ix_audit_events_campaign_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_leads_campaign_id", table_name="leads")
    op.drop_table("leads")
    op.drop_table("campaigns")
