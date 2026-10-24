"""Add campaign USD annual-revenue filters instead of employee-count constraints.

Revision ID: 0006_revenue_criteria
Revises: 0005_market_playbooks
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_revenue_criteria"
down_revision = "0005_market_playbooks"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("min_revenue_usd", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_revenue_usd", sa.BigInteger(), nullable=False, server_default="0"))
    # The old headcount columns are deliberately retained for historical
    # records. New campaigns do not populate or use them for scoring.


def downgrade():
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_column("max_revenue_usd")
        batch.drop_column("min_revenue_usd")
