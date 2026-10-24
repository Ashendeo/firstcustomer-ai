"""Outreach policy, suppression, delivery ledger and inbound replies.

Revision ID: 0003_autonomous_outreach
Revises: 0002_checkpoints
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_autonomous_outreach"
down_revision = "0002_checkpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outreach_policies",
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("auto_send", sa.Boolean(), nullable=False),
        sa.Column("min_score", sa.Integer(), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("prepare_linkedin_drafts", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("legal_basis", sa.String(45), nullable=False),
        sa.Column("organization_type", sa.String(45), nullable=False),
        sa.Column("sender_postal_address", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.String(254), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "outreach_suppressions",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outreach_attempts",
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("day_utc", sa.String(10), nullable=False),
        sa.Column("day_slot", sa.Integer(), nullable=False),
        sa.UniqueConstraint("day_utc", "day_slot", name="uq_outreach_global_day_slot"),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("provider_id", sa.String(150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outreach_attempts_campaign_id", "outreach_attempts", ["campaign_id"])
    op.create_index("ix_outreach_attempts_email", "outreach_attempts", ["email"])
    op.create_index("ix_outreach_attempts_day_utc", "outreach_attempts", ["day_utc"])
    op.create_table(
        "processed_resend_events",
        sa.Column("event_id", sa.String(150), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "inbound_replies",
        sa.Column("message_id", sa.String(150), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender", sa.String(254), nullable=False),
        sa.Column("classification", sa.String(45), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inbound_replies_lead_id", "inbound_replies", ["lead_id"])


def downgrade():
    op.drop_index("ix_inbound_replies_lead_id", table_name="inbound_replies")
    op.drop_table("inbound_replies")
    op.drop_table("processed_resend_events")
    op.drop_index("ix_outreach_attempts_day_utc", table_name="outreach_attempts")
    op.drop_index("ix_outreach_attempts_email", table_name="outreach_attempts")
    op.drop_index("ix_outreach_attempts_campaign_id", table_name="outreach_attempts")
    op.drop_table("outreach_attempts")
    op.drop_table("outreach_suppressions")
    op.drop_table("outreach_policies")
