"""Pinned local customisation templates; no product/Shopify changes."""
from alembic import op
import sqlalchemy as sa

revision = '0015_customisation_templates'
down_revision = '0014_shopify_sales'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('customisation_template',
        sa.Column('template_id', sa.String(64), primary_key=True),
        sa.Column('revision', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(191), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('definition_json', sa.Text(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('revision > 0', name='ck_custom_template_revision'),
        sa.CheckConstraint('length(definition_json) <= 1048576', name='ck_custom_template_size'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT count(*) FROM customisation_template')).scalar():
        raise RuntimeError('Pinned template revisions may be referenced by authored JSON; archive/export before reviewed downgrade.')
    op.drop_table('customisation_template')
