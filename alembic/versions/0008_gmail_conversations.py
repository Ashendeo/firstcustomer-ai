"""Gmail conversation journal and durable message outbox."""
from alembic import op
import sqlalchemy as sa
revision = '0008_gmail_conversations'
down_revision = '0007_company_lookup'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gmail_conversations',
        sa.Column('lead_id', sa.String(36), sa.ForeignKey('leads.id'), primary_key=True),
        sa.Column('account', sa.String(254), nullable=False),
        sa.Column('recipient', sa.String(254), nullable=False),
        sa.Column('sandbox', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('gmail_messages',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('lead_id', sa.String(36), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('message_id', sa.String(254), unique=True, nullable=False),
        sa.Column('direction', sa.String(12), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('reply_to_id', sa.String(254), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('gmail_send_slots', sa.Column('message_id', sa.String(64), primary_key=True), sa.Column('day', sa.String(10), nullable=False), sa.Column('slot', sa.Integer(), nullable=False), sa.UniqueConstraint('day', 'slot', name='uq_gmail_send_slot'))
    op.create_index('ix_gmail_messages_lead_id', 'gmail_messages', ['lead_id'])


def downgrade():
    op.drop_table('gmail_send_slots')
    op.drop_table('gmail_messages')
    op.drop_table('gmail_conversations')
