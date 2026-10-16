"""Read-only Shopify Orders persistence and existing-coordinator task."""
from alembic import op
import sqlalchemy as sa

revision = '0016_shopify_orders'
down_revision = '0015_customisation_templates'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('shopify_order',
        sa.Column('id', sa.Integer(), primary_key=True),
        *[sa.Column(k, sa.String(n), nullable=False) for k, n in (
            ('store_id',96),('remote_id',96),('name',100),('api_version',10),
            ('financial_status',40),('fulfillment_status',40),('currency',3))],
        *[sa.Column(k, sa.DateTime(), nullable=False) for k in ('created_at','updated_at','synced_at')],
        *[sa.Column(k, sa.String(n)) for k,n in (('customer_id',96),('customer_name',255),('email',320),('phone',100),('cancel_reason',100))],
        sa.Column('cancelled_at',sa.DateTime()), sa.Column('redacted_at',sa.DateTime()),
        *[sa.Column(k,sa.Text(),nullable=False) for k in ('delivery_json','money_json','shipping_json')],
        sa.Column('test',sa.Boolean(),nullable=False),sa.Column('personalised',sa.Boolean(),nullable=False),
        sa.Column('total',sa.Numeric(18,4),nullable=False),
        sa.Column('quantity',sa.Integer(),nullable=False),sa.Column('outstanding_quantity',sa.Integer(),nullable=False),
        sa.UniqueConstraint('store_id','remote_id',name='uq_order_store_gid'),
        sa.CheckConstraint('quantity >= 0 AND outstanding_quantity >= 0',name='ck_order_quantities'))
    op.create_index('ix_order_store_created','shopify_order',['store_id','created_at','id'])
    op.create_index('ix_order_store_status','shopify_order',['store_id','test','fulfillment_status','financial_status'])
    op.create_index('ix_order_name','shopify_order',['name'])
    op.create_index('ix_order_created','shopify_order',['created_at','id'])
    op.create_index('ix_order_operational','shopify_order',['test','cancelled_at','financial_status','outstanding_quantity'])
    op.create_table('shopify_order_line',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('order_id',sa.Integer(),sa.ForeignKey('shopify_order.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('remote_id',sa.String(96),nullable=False),
        sa.Column('product_id',sa.String(96)),sa.Column('variant_id',sa.String(96)),
        sa.Column('sku',sa.String(255)),sa.Column('title',sa.String(1024),nullable=False),sa.Column('variant_title',sa.String(1024)),
        *[sa.Column(k,sa.Integer(),nullable=False) for k in ('position','quantity','current_quantity','unfulfilled_quantity','refundable_quantity')],
        *[sa.Column(k,sa.Boolean(),nullable=False) for k in ('is_current','personalised')],
        *[sa.Column(k,sa.Text(),nullable=False) for k in ('money_json','properties_json','personalisation_json')],
        sa.UniqueConstraint('order_id','remote_id',name='uq_order_line_gid'),
        sa.CheckConstraint('quantity >= 0 AND current_quantity >= 0 AND unfulfilled_quantity >= 0 AND refundable_quantity >= 0',name='ck_order_line_quantities'))
    op.create_index('ix_order_line_sku','shopify_order_line',['sku','order_id'])
    # Existing catalogue connections don't globally enable SQLite FK enforcement.
    # Enforce this new protected boundary without changing legacy scanner deletes.
    for suffix,event in [('insert','INSERT'),('update','UPDATE OF order_id')]:
        op.execute(sa.text(f"CREATE TRIGGER trg_order_line_parent_{suffix} BEFORE {event} ON shopify_order_line "
            "WHEN NOT EXISTS (SELECT 1 FROM shopify_order WHERE id=NEW.order_id) "
            "BEGIN SELECT RAISE(ABORT, 'Order line parent is required'); END"))
    op.execute(sa.text("CREATE TRIGGER trg_order_parent_restrict BEFORE DELETE ON shopify_order "
        "WHEN EXISTS (SELECT 1 FROM shopify_order_line WHERE order_id=OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'Retained order lines prevent parent deletion'); END"))
    op.create_table('shopify_order_sync',sa.Column('store_id',sa.String(96),primary_key=True),
        sa.Column('domain',sa.String(255),nullable=False),
        *[sa.Column(k,sa.DateTime()) for k in ('checkpoint','window_start','window_end','attempted_at','successful_at')],
        sa.Column('cursor',sa.Text()),sa.Column('page_cursor',sa.Text()),
        sa.Column('pending_json',sa.Text(),nullable=False),sa.Column('page_more',sa.Boolean(),nullable=False),
        sa.Column('state',sa.String(32),nullable=False),sa.Column('error',sa.String(500)),sa.Column('imported',sa.Integer(),nullable=False),
        sa.CheckConstraint('length(pending_json) <= 32768',name='ck_order_sync_bound'))
    op.execute(sa.text("INSERT INTO scheduled_task (key,enabled,interval_minutes,next_due,state,selection_cursor) VALUES ('shopify_orders',1,5,datetime('now','+5 minutes'),'idle',0)"))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT count(*) FROM shopify_order')).scalar():
        raise RuntimeError('Orders contain historical purchase snapshots. Export/back up and obtain approval before destructive downgrade.')
    op.execute(sa.text("DELETE FROM scheduled_task_run WHERE task_key='shopify_orders'"))
    op.execute(sa.text("DELETE FROM scheduled_task WHERE key='shopify_orders'"))
    op.drop_table('shopify_order_sync')
    op.drop_table('shopify_order_line')
    op.drop_table('shopify_order')
