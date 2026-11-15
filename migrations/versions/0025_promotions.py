"""Local promotion definitions and retained Shopify publication evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0025_promotions'
down_revision = '0024_production_cost_basis'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('promotion',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('document', sa.Text(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('receipt', sa.Text()),
        sa.Column('updated_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('promotion')
