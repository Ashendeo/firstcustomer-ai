"""Seller identity and honest nullable prospect evidence scores.

Revision ID: 0004_seller_research
Revises: 0003_autonomous_outreach
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_seller_research"
down_revision = "0003_autonomous_outreach"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("seller_name", sa.String(200), nullable=False, server_default=""))
        batch.add_column(sa.Column("seller_domain", sa.String(180), nullable=False, server_default=""))
    with op.batch_alter_table("leads") as batch:
        batch.alter_column("fit_score", existing_type=sa.Integer(),
                           nullable=True, server_default=None)


def downgrade():
    # Retain old compatibility without converting unscorable NULL into an
    # arbitrary score: downgrade must be operator-reviewed if NULLs exist.
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT COUNT(*) FROM leads WHERE fit_score IS NULL")).scalar():
        raise RuntimeError("Cannot downgrade while unscored leads exist; review data first")
    with op.batch_alter_table("leads") as batch:
        batch.alter_column("fit_score", existing_type=sa.Integer(),
                           nullable=False, server_default=None)
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_column("seller_domain")
        batch.drop_column("seller_name")
