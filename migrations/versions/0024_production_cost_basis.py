"""Track cost-only revision changes without rescanning or rewriting catalogue JSON."""
from alembic import op
import sqlalchemy as sa

revision = '0024_production_cost_basis'
down_revision = '0023_unassigned_stock'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shopify_publish_job', sa.Column('production_basis_revision', sa.String(64)))
    op.add_column('shopify_publish_job', sa.Column('production_checked_at', sa.DateTime()))
    op.create_index('ix_publish_job_production_due', 'shopify_publish_job', ['production_checked_at', 'product_id'],
                    sqlite_where=sa.text('production_basis_revision IS NOT NULL'))


def downgrade():
    op.drop_index('ix_publish_job_production_due', table_name='shopify_publish_job')
    with op.batch_alter_table('shopify_publish_job') as batch:
        batch.drop_column('production_checked_at')
        batch.drop_column('production_basis_revision')
