"""Manage existing app webhooks and explicitly opt-in order status projection."""
from alembic import op
import sqlalchemy as s
revision='0029_order_integrations'
down_revision='0028_order_fulfilment'
branch_labels=depends_on=None


def upgrade():
    op.create_index('ix_order_event_identity','shopify_order_event',['store_id','topic','event_id'])
    op.create_table('order_webhook_setting',s.Column('id',s.Integer(),primary_key=True),
        s.Column('store_id',s.String(96),nullable=False),s.Column('topic',s.String(40),nullable=False),
        s.Column('name',s.String(120),nullable=False),s.Column('endpoint',s.String(500),nullable=False),
        s.Column('enabled',s.Boolean(),nullable=False),s.Column('version',s.Integer(),nullable=False),
        s.Column('remote_id',s.String(96),unique=True),s.Column('remote_json',s.Text(),nullable=False),
        s.Column('state',s.String(32),nullable=False),s.Column('error',s.String(400),nullable=False),
        s.Column('verified_at',s.DateTime()),s.Column('operation_until',s.DateTime()),s.UniqueConstraint('store_id','topic','endpoint'))
    op.create_table('order_integration_settings',s.Column('store_id',s.String(96),primary_key=True),
        s.Column('projection_enabled',s.Boolean(),nullable=False),s.Column('fingerprint',s.String(64),nullable=False),
        s.Column('actor',s.String(160),nullable=False),s.Column('authorised_at',s.DateTime(),nullable=False))
    op.create_table('order_status_projection',s.Column('order_id',s.Integer(),s.ForeignKey('shopify_order.id'),primary_key=True),
        s.Column('value',s.String(120)),s.Column('state',s.String(32),nullable=False),s.Column('error',s.String(400),nullable=False),
        s.Column('attempts',s.Integer(),nullable=False),s.Column('verified_at',s.DateTime()),s.Column('next_attempt',s.DateTime()),
        s.Column('owner',s.String(32)),s.Column('lease_until',s.DateTime()))
    # 0018 retired polling unconditionally. This new, explicitly requested
    # capability installs the one existing task at its new safe default, once.
    op.execute("UPDATE scheduled_task SET enabled=1,interval_minutes=15,next_due=datetime('now','+15 minutes'),state=CASE WHEN active_run_id IS NULL THEN 'idle' ELSE state END WHERE key='shopify_orders'")


def downgrade():
    raise RuntimeError('Retained order integration evidence requires a paired backup; destructive downgrade refused.')
