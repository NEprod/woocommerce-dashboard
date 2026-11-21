"""Persist byte fingerprints of catalogue-owned image assets."""
from alembic import op
import sqlalchemy as sa

revision = '0026_image_fingerprints'
down_revision = '0025_promotions'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('product_asset', sa.Column('content_sha256', sa.String(64)))


def downgrade():
    with op.batch_alter_table('product_asset') as batch:
        batch.drop_column('content_sha256')
