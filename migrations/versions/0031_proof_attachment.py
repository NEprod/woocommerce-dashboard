"""Private proof images retained with existing proof versions in appdata SQLite."""
from alembic import op
import sqlalchemy as sa
revision='0031_proof_attachment'
down_revision='0030_order_workflow'
branch_labels=depends_on=None


def upgrade():
    op.add_column('order_work_proof',sa.Column('file_data',sa.LargeBinary(),nullable=True))


def downgrade():
    raise RuntimeError('Proof files are retained business data; restore a paired backup instead.')
