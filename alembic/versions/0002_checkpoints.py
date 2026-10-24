"""Durable campaign checkpoint journal and stable lead keys.

Revision ID: 0002_checkpoints
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_checkpoints"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("discovery_key", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_leads_campaign_discovery",
                                       ["campaign_id", "discovery_key"])
    op.create_table(
        "step_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("step_key", sa.String(160), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "step_key", name="uq_checkpoint_campaign_step"),
    )
    op.create_index("ix_step_checkpoints_campaign_id", "step_checkpoints",
                    ["campaign_id"])


def downgrade():
    op.drop_index("ix_step_checkpoints_campaign_id", table_name="step_checkpoints")
    op.drop_table("step_checkpoints")
    with op.batch_alter_table("leads") as batch:
        batch.drop_constraint("uq_leads_campaign_discovery", type_="unique")
        batch.drop_column("discovery_key")
