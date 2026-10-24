"""Shared seller-domain market buying-criteria skills.

Revision ID: 0005_market_playbooks
Revises: 0004_seller_research
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_market_playbooks"
down_revision = "0004_seller_research"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_playbooks",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("seller_domain", sa.String(180), nullable=False),
        sa.Column("product_hash", sa.String(64), nullable=False),
        sa.Column("market_domain", sa.String(90), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("research", sa.JSON(), nullable=False),
        sa.Column("rubric", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_market_playbooks_seller_domain", "market_playbooks", ["seller_domain"])


def downgrade():
    op.drop_index("ix_market_playbooks_seller_domain", table_name="market_playbooks")
    op.drop_table("market_playbooks")
