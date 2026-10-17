"""Optional solo packaging; no basket/rate tables or authored catalogue changes."""
from alembic import op
import sqlalchemy as sa
revision='0017_packaging'
down_revision='0016_shopify_orders'
branch_labels=None
depends_on=None


def upgrade():
    package=op.create_table('packaging_package',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('seed_key',sa.String(60),unique=True),sa.Column('name',sa.String(191),nullable=False),
        sa.Column('archived',sa.Boolean(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('specification',sa.Text(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False))
    op.create_index('ix_packaging_package_name','packaging_package',['name'])
    op.create_table('packaging_link',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('package_id',sa.String(36),sa.ForeignKey('packaging_package.id'),nullable=False),
        sa.Column('store_id',sa.String(96),nullable=False),sa.Column('remote_id',sa.String(96),nullable=False),
        sa.Column('observed',sa.Text(),nullable=False),sa.Column('checked_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('store_id','remote_id'),sa.UniqueConstraint('store_id','package_id'))
    op.create_table('packaging_evidence',sa.Column('key',sa.String(100),primary_key=True),
        sa.Column('value',sa.Text(),nullable=False),sa.Column('checked_at',sa.DateTime(),nullable=False))
    from app.packaging_catalogue import initial_rows
    op.bulk_insert(package,list(initial_rows()))


def downgrade():
    raise RuntimeError('Packaging contains retained operational identities/history. Restore a backup for rollback; no destructive downgrade.')
