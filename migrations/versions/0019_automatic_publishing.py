"""Durable disabled-by-default publishing authority, coalescing work and due events."""
from alembic import op
import sqlalchemy as sa

revision = '0019_automatic_publishing'
down_revision = '0018_orders_webhooks'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('automatic_publishing',
        sa.Column('key', sa.String(32), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('config_digest', sa.String(64)), sa.Column('store_id', sa.String(100)),
        sa.Column('domain', sa.String(253)), sa.Column('actor_id', sa.Integer(), sa.ForeignKey('user.id')),
        sa.Column('required_fields_json', sa.Text(), nullable=False), sa.Column('updated_at', sa.DateTime()))
    op.bulk_insert(sa.table('automatic_publishing', sa.column('key'), sa.column('enabled', sa.Boolean()),
        sa.column('revision', sa.Integer()), sa.column('required_fields_json')),
        [{'key': 'shopify', 'enabled': False, 'revision': 0, 'required_fields_json': '[]'}])
    op.create_table('shopify_publish_job',
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('product.id'), primary_key=True),
        sa.Column('source_identity', sa.String(1024), nullable=False),
        sa.Column('requested_revision', sa.String(64), nullable=False),
        sa.Column('verified_revision', sa.String(64)), sa.Column('running_revision', sa.String(64)),
        sa.Column('generation', sa.Integer(), nullable=False), sa.Column('state', sa.String(32), nullable=False),
        sa.Column('trigger', sa.String(32), nullable=False), sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('owner', sa.String(100)), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt', sa.DateTime(), nullable=False),
        sa.Column('operation_id', sa.String(32), sa.ForeignKey('catalogue_operation.id')),
        sa.Column('manual_actor_id', sa.Integer(), sa.ForeignKey('user.id')),
        sa.Column('manual_revision', sa.String(64)), sa.Column('manual_config', sa.String(64)),
        sa.Column('error', sa.String(1000)), sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('view_json', sa.Text()), sa.Column('image_available', sa.Boolean()),
        sa.Column('projected_at', sa.DateTime()), sa.Column('view_revision', sa.String(64)),
        sa.Column('view_pending', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('scan_operation_id', sa.String(32)))
    op.create_index('ix_shopify_publish_job_state', 'shopify_publish_job', ['state'])
    op.create_index('ix_shopify_publish_job_next_attempt', 'shopify_publish_job', ['next_attempt'])
    op.create_index('ix_shopify_publish_job_view_pending', 'shopify_publish_job', ['view_pending'])
    op.create_table('shopify_due_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('product.id'), nullable=False),
        sa.Column('event_type', sa.String(32), nullable=False),
        sa.Column('schedule_revision', sa.String(64), nullable=False),
        sa.Column('snapshot_guard', sa.String(64), nullable=False),
        sa.Column('due_at', sa.DateTime(), nullable=False), sa.Column('state', sa.String(32), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False), sa.Column('error', sa.String(1000)),
        sa.Column('operation_id', sa.String(32), sa.ForeignKey('catalogue_operation.id')),
        sa.UniqueConstraint('product_id', 'event_type'))
    op.create_index('ix_shopify_due_pending', 'shopify_due_event', ['state', 'due_at'])
    op.create_table('shopify_publishing_event',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('operation_id', sa.String(32), sa.ForeignKey('catalogue_operation.id'), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('product.id')),
        sa.Column('event_type', sa.String(40), nullable=False), sa.Column('trigger', sa.String(32), nullable=False),
        sa.Column('local_revision', sa.String(64)), sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('receipt_json', sa.Text(), nullable=False))
    op.create_index('ix_shopify_publishing_event_operation_id', 'shopify_publishing_event', ['operation_id'])
    task = sa.table('scheduled_task', sa.column('key'), sa.column('enabled', sa.Boolean()),
        sa.column('interval_minutes', sa.Integer()), sa.column('next_due', sa.DateTime()),
        sa.column('state'), sa.column('selection_cursor', sa.Integer()))
    from datetime import datetime, UTC
    op.bulk_insert(task, [{'key': 'shopify_publisher', 'enabled': False, 'interval_minutes': 60,
                         'next_due': datetime.now(UTC).replace(tzinfo=None), 'state': 'disabled', 'selection_cursor': 0}])
    op.bulk_insert(task, [{'key': 'catalogue_views', 'enabled': True, 'interval_minutes': 5,
                         'next_due': datetime.now(UTC).replace(tzinfo=None), 'state': 'idle', 'selection_cursor': 0}])
    # Intentional new reconciliation default, preserving active resumable work.
    op.execute("UPDATE scheduled_task SET interval_minutes=1440 WHERE key='shopify_products' AND interval_minutes=20")


def downgrade():
    raise RuntimeError('Publishing authority and recovery evidence: downgrade refused; restore a matching backup instead.')
