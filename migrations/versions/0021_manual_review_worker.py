"""Manual-only worker slot reuses durable scheduler leases and operation receipts."""
from alembic import op
import sqlalchemy as sa

revision='0021_manual_review_worker'
down_revision='0020_operations_acceptance'
branch_labels=depends_on=None


def upgrade():
    op.execute(sa.text("INSERT INTO scheduled_task (key,enabled,interval_minutes,next_due,state,selection_cursor) VALUES ('shopify_manual_review',0,60,datetime('now'),'idle',0)"))


def downgrade():
    raise RuntimeError('Retained manual approvals require a paired backup; automatic downgrade refused.')
