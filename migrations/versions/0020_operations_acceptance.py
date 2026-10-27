"""Separate release coordination and bounded operations-history indexes."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, UTC

revision = '0020_operations_acceptance'
down_revision = '0019_automatic_publishing'
branch_labels = depends_on = None


def upgrade():
    task = sa.table('scheduled_task', sa.column('key'), sa.column('enabled', sa.Boolean()),
        sa.column('interval_minutes', sa.Integer()), sa.column('next_due', sa.DateTime()),
        sa.column('state'), sa.column('selection_cursor', sa.Integer()))
    op.bulk_insert(task, [{'key': 'shopify_releases', 'enabled': True, 'interval_minutes': 5,
        'next_due': datetime.now(UTC).replace(tzinfo=None), 'state': 'idle', 'selection_cursor': 0}])
    # Never change a retained merchant interval or rewrite migration 0019.
    op.create_index('ix_publishing_events_time', 'shopify_publishing_event', ['occurred_at', 'id'])
    op.create_index('ix_scheduled_history_key_time', 'scheduled_task_run', ['task_key', 'created_at'])


def downgrade():
    raise RuntimeError('Operations evidence downgrade refused; restore a matching backup.')
