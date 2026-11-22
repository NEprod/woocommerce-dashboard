"""Local order work, proof evidence and ledger-linked picking; commerce untouched."""
import sqlalchemy as s
from alembic import op

revision='0028_order_fulfilment'
down_revision='0027_production_batches'
branch_labels=depends_on=None


def upgrade():
    op.create_table('order_work_line',
        s.Column('line_id',s.Integer(),s.ForeignKey('shopify_order_line.id'),primary_key=True),
        s.Column('order_id',s.Integer(),s.ForeignKey('shopify_order.id'),nullable=False),
        s.Column('product_id',s.Integer(),s.ForeignKey('product.id')),
        s.Column('state',s.String(40),nullable=False),s.Column('policy_json',s.Text(),nullable=False),
        s.Column('bom_json',s.Text(),nullable=False),s.Column('source_digest',s.String(64),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('proof_required',s.Boolean(),nullable=False),
        s.Column('committed_at',s.DateTime()),s.Column('committed_by',s.String(160)),
        s.Column('hold_reason',s.String(400),nullable=False),s.Column('prior_state',s.String(40)))
    op.create_index('ix_order_work_line_order_id','order_work_line',['order_id'])
    op.create_table('order_work_requirement',s.Column('id',s.Integer(),primary_key=True),
        s.Column('line_id',s.Integer(),s.ForeignKey('order_work_line.line_id'),nullable=False),
        s.Column('item_id',s.Integer(),s.ForeignKey('inventory_item.id'),nullable=False),
        s.Column('quantity',s.BigInteger(),nullable=False),s.Column('reserved',s.BigInteger(),nullable=False),
        s.Column('active',s.Boolean(),nullable=False),
        s.Column('unit',s.String(8),nullable=False),s.UniqueConstraint('line_id','item_id'),
        s.CheckConstraint('quantity > 0 AND reserved >= 0 AND reserved <= quantity'))
    op.create_table('order_work_event',s.Column('id',s.String(32),primary_key=True),
        s.Column('line_id',s.Integer(),s.ForeignKey('order_work_line.line_id'),nullable=False),
        *[s.Column(k,s.String(n),nullable=False) for k,n in [('action',40),('previous',40),('state',40),('actor',160),('reason',400),('digest',64)]],
        s.Column('at',s.DateTime(),nullable=False),s.Column('details',s.Text(),nullable=False))
    op.create_table('order_work_proof',s.Column('id',s.Integer(),primary_key=True),
        s.Column('line_id',s.Integer(),s.ForeignKey('order_work_line.line_id'),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('reference',s.String(100),nullable=False),
        s.Column('state',s.String(32),nullable=False),s.Column('method',s.String(100),nullable=False),
        s.Column('evidence',s.String(400),nullable=False),s.Column('actor',s.String(160),nullable=False),
        s.Column('created_at',s.DateTime(),nullable=False),s.Column('sent_at',s.DateTime()),
        s.Column('approved_at',s.DateTime()),s.UniqueConstraint('line_id','version'))
    op.create_table('order_work_pick',s.Column('id',s.String(32),s.ForeignKey('order_work_event.id'),primary_key=True),
        s.Column('requirement_id',s.Integer(),s.ForeignKey('order_work_requirement.id'),nullable=False),
        s.Column('transaction_id',s.String(32),s.ForeignKey('inventory_transaction.id'),nullable=False,unique=True),
        s.Column('quantity',s.BigInteger(),nullable=False),
        s.Column('reverses_id',s.String(32),s.ForeignKey('order_work_pick.id'),unique=True),s.CheckConstraint('quantity != 0'))
    op.create_table('order_work_plan',s.Column('id',s.String(32),primary_key=True),
        s.Column('requirement_id',s.Integer(),s.ForeignKey('order_work_requirement.id')),
        s.Column('item_id',s.Integer(),s.ForeignKey('inventory_item.id'),nullable=False),
        s.Column('kind',s.String(16),nullable=False),s.Column('quantity',s.BigInteger(),nullable=False),
        s.Column('document',s.Text(),nullable=False),s.Column('state',s.String(20),nullable=False),
        s.Column('actor',s.String(160),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('digest',s.String(64),nullable=False))
    op.create_table('order_work_outbound',s.Column('id',s.Integer(),primary_key=True),
        s.Column('event_id',s.String(32),s.ForeignKey('order_work_event.id'),nullable=False),
        s.Column('channel',s.String(16),nullable=False),s.Column('state',s.String(20),nullable=False),
        s.Column('attempts',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),
        s.Column('error',s.String(400),nullable=False),s.Column('delivered_at',s.DateTime()),s.UniqueConstraint('event_id','channel'))
    for table,columns in [('order_work_requirement',['line_id','item_id']),('order_work_event',['line_id']),
                          ('order_work_proof',['line_id']),('order_work_pick',['requirement_id']),('order_work_plan',['item_id'])]:
        for column in columns:op.create_index('ix_'+table+'_'+column,table,[column])
    for table in ('order_work_event','order_work_pick'):
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Operational audit evidence is append-only'); END")


def downgrade():
    raise RuntimeError('Retain operational history; restore a paired backup instead of dropping it.')
