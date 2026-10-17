"""Durable encrypted webhook inbox and startup/manual Orders diagnostics."""
from alembic import op
import sqlalchemy as sa
revision='0018_orders_webhooks'
down_revision='0017_packaging'
branch_labels=None
depends_on=None


def upgrade():
    for name,kind in [('startup_successful_at',sa.DateTime()),('manual_successful_at',sa.DateTime()),
                      ('error_category',sa.String(40)),('error_phase',sa.String(40))]:
        op.add_column('shopify_order_sync',sa.Column(name,kind))
    op.create_table('shopify_order_event',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('store_id',sa.String(96),nullable=False),sa.Column('delivery_id',sa.String(128),nullable=False),
        sa.Column('topic',sa.String(40),nullable=False),sa.Column('order_gid',sa.String(96)),
        sa.Column('event_id',sa.String(128)),sa.Column('triggered_at',sa.DateTime()),
        sa.Column('source_updated_at',sa.DateTime()),sa.Column('received_at',sa.DateTime(),nullable=False),
        sa.Column('processed_at',sa.DateTime()),sa.Column('digest',sa.String(64),nullable=False),
        sa.Column('payload',sa.Text()),sa.Column('state',sa.String(32),nullable=False),
        sa.Column('attempts',sa.Integer(),nullable=False),sa.Column('error',sa.String(500)),
        sa.UniqueConstraint('store_id','delivery_id'))
    op.create_index('ix_order_event_pending','shopify_order_event',['state','id'])
    op.create_index('ix_order_event_order','shopify_order_event',['store_id','order_gid'])
    op.create_table('shopify_order_delivery',sa.Column('key',sa.String(32),primary_key=True),
        sa.Column('owner',sa.String(100)),sa.Column('lease_until',sa.DateTime()),sa.Column('next_pull',sa.DateTime()),
        sa.Column('received_at',sa.DateTime()),sa.Column('processed_at',sa.DateTime()),sa.Column('error',sa.String(500)))
    op.bulk_insert(sa.table('shopify_order_delivery',sa.column('key',sa.String())),[{'key':'orders'}])
    # Retire only the obsolete recurring trigger, retaining manual/startup work.
    op.execute("UPDATE scheduled_task_run SET state='partially_completed', error='Recurring Orders polling retired; use startup/manual catch-up.' WHERE task_key='shopify_orders' AND trigger='scheduled' AND state IN ('queued','running','paused')")
    op.execute("UPDATE scheduled_task SET active_run_id=NULL, owner=NULL, lease_until=NULL, resume_at=NULL WHERE key='shopify_orders' AND active_run_id IN (SELECT id FROM scheduled_task_run WHERE task_key='shopify_orders' AND trigger='scheduled')")
    op.execute("UPDATE scheduled_task SET enabled=0, state=CASE WHEN active_run_id IS NULL THEN 'disabled' ELSE state END WHERE key='shopify_orders'")


def downgrade():
    raise RuntimeError('Retained order events: downgrade refused; restore the matching backup instead.')
