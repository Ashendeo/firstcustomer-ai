"""Shared cached name-to-company search.

Revision ID: 0007_company_lookup
Revises: 0006_revenue_criteria
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_company_lookup"
down_revision = "0006_revenue_criteria"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("seller_lookups",
        sa.Column("lookup_key", sa.String(64), primary_key=True),
        sa.Column("normalized_query", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("seller_lookups")
