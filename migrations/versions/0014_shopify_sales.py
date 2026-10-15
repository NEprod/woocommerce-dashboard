"""Reviewed sale authority/transition identity; reuse task and operation ledgers."""
from alembic import op
import sqlalchemy as sa

revision='0014_shopify_sales'
down_revision='0013_scheduled_tasks'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('shopify_sale_schedule',
        sa.Column('id',sa.String(32),primary_key=True),
        sa.Column('product_id',sa.Integer(),sa.ForeignKey('product.id',ondelete='SET NULL')),
        sa.Column('store_id',sa.String(128),nullable=False),
        sa.Column('source_identity',sa.String(1024),nullable=False),
        sa.Column('identity_id',sa.Integer(),sa.ForeignKey('shopify_product_identity.id'),nullable=False),
        sa.Column('generation',sa.String(32),nullable=False),sa.Column('state',sa.String(32),nullable=False),
        *[sa.Column(k,sa.DateTime()) for k in ('start_at','end_at','next_due')],
        sa.Column('authorised_at',sa.DateTime(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False),
        sa.Column('operation_id',sa.String(32),sa.ForeignKey('catalogue_operation.id')),
        sa.Column('evidence_json',sa.Text(),nullable=False),sa.Column('error',sa.String(1000)),
        sa.UniqueConstraint('store_id','source_identity',name='uq_shopify_sale_store_source'),
        sa.CheckConstraint('length(evidence_json)<=65536',name='ck_shopify_sale_bound'))
    op.create_index('ix_shopify_sale_schedule_product_id','shopify_sale_schedule',['product_id'])
    op.create_index('ix_shopify_sale_schedule_next_due','shopify_sale_schedule',['next_due'])
    op.execute(sa.text("INSERT INTO scheduled_task (key,enabled,interval_minutes,next_due,state,selection_cursor) "
                       "VALUES ('shopify_sales',1,5,datetime('now','+5 minutes'),'idle',0)"))


def downgrade():
    connection=op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM shopify_sale_schedule")).scalar():
        raise RuntimeError('Retained sale authorisations/receipts require reviewed archival; automatic downgrade refused.')
    op.execute(sa.text("DELETE FROM scheduled_task_run WHERE task_key='shopify_sales'"))
    op.execute(sa.text("DELETE FROM scheduled_task WHERE key='shopify_sales'"))
    op.drop_table('shopify_sale_schedule')
